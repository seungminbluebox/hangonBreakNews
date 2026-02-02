import os
import sys
import time
import json
import calendar
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from newspaper import Article, Config
import google.generativeai as genai

# 상위 디렉토리 참조 추가
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from push_notification import send_push_to_all

load_dotenv()

# 환경 변수 및 설정
GEMINI_MODEL_NAME= os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-pro")
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL_NAME)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 감시할 뉴스 소스 (RSS) - 실시간 '속보' 전용 시스템으로 전면 교체
RSS_FEEDS = [
    # 1. Google News - 초단위 속보 검색 (검색 쿼리에 'breaking news' 강제)
    "https://news.google.com/rss/search?q=intitle:%22breaking+news%22+OR+intitle:%22속보%22+when:1h&hl=en-US&gl=US&ceid=US:en",
    
    # 2. Yahoo Finance - 속보(Latest) 전용 섹션 RSS
    "https://finance.yahoo.com/news/rss",
]

# 메모리 상에서 이미 처리한 뉴스 타임스탬프 또는 제목 저장 (중복 방지)
processed_news = set()

def get_recent_news_titles():
    """DB에서 최근 20개의 속보 제목을 가져옵니다."""
    try:
        res = supabase.table("breaking_news").select("title").order("created_at", desc=True).limit(20).execute()
        return [item['title'] for item in res.data]
    except Exception as e:
        print(f"Error fetching recent titles: {e}")
        return []

def fetch_latest_headlines():
    headlines = []
    # 1. 기준 시간 설정 (모두 UTC로 통일하여 정확하게 30분 필터링)
    now_utc = datetime.now(timezone.utc)
    time_limit_utc = now_utc - timedelta(minutes=30)
    
    # 속보를 나타내는 핵심 키워드 (입구 컷용)
    BREAKING_KEYWORDS = ["속보", "breaking", "urgent", "just in", "alert", "flash", "급보", "공시", "[특징주]"]
    
    custom_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    
    # 2. RSS 피드 수집 (Global/Google 속보 피드)
    for i, url in enumerate(RSS_FEEDS, 1):
        try:
            feed = feedparser.parse(url, agent=custom_agent)
            entries_found = len(feed.entries)
            print(f"📡 Source {i} (RSS) checking: {entries_found} entries found.")
            
            for entry in feed.entries:
                title_lower = entry.title.lower()
                
                # [필터 1] 제목에 '속보' 관련 키워드가 포함된 것만 1차 선별
                if not any(kw in title_lower for kw in BREAKING_KEYWORDS):
                    continue

                pub_datetime_utc = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_ts = calendar.timegm(entry.published_parsed)
                    pub_datetime_utc = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                
                is_recent = False
                if pub_datetime_utc:
                    if pub_datetime_utc >= time_limit_utc:
                        is_recent = True
                else:
                    is_recent = True
                
                if is_recent:
                    headlines.append({
                        "title": entry.title,
                        "link": entry.link,
                        "source": "Global/RSS Feed"
                    })
        except Exception as e:
            print(f"Error fetching RSS {url}: {e}")

    # 3. 국내 속보 (네이버 금융) - KST를 UTC로 변환하여 동기화
    try:
        url = "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258"
        headers = {"User-Agent": custom_agent}
        res = requests.get(url, headers=headers)
        res.encoding = 'cp949' 
        soup = BeautifulSoup(res.text, "html.parser")
        
        kst = timezone(timedelta(hours=9))
        news_items = soup.select("ul.realtimeNewsList > li")
        print(f"🇰🇷 Naver Finance checking: {len(news_items)} entries found.")
        
        for item in news_items:
            subject_tag = item.select_one(".articleSubject a")
            wdate_tag = item.select_one(".wdate")
            
            if subject_tag and wdate_tag:
                title = subject_tag.text.strip()
                title_lower = title.lower()

                # [필터 1] 네이버 뉴스도 제목에 '속보' 키워드가 있는 것만 선별
                if not any(kw in title_lower for kw in BREAKING_KEYWORDS):
                    continue

                link = "https://finance.naver.com" + subject_tag['href']
                date_str = wdate_tag.text.strip().replace(".", "-")
                
                try:
                    pub_time_kst = datetime.strptime(date_str, "%Y-%m-%d %H:%M").replace(tzinfo=kst)
                    pub_time_utc = pub_time_kst.astimezone(timezone.utc)
                    
                    if pub_time_utc >= time_limit_utc:
                        headlines.append({
                            "title": title,
                            "link": link,
                            "source": "Naver Finance (Strict)"
                        })
                except: pass
    except Exception as e:
        print(f"Error fetching Naver breaking news: {e}")

    return headlines

def filter_breaking_news(headlines, recent_titles):
    """
    Gemini AI를 사용하여 수집된 뉴스 중 진짜 '속보' 가치가 있는 것만 선별합니다.
    최근에 이미 보도된 내용과 겹치는지 체크합니다.
    """
    if not headlines:
        return []

    prompt = f"""
    당신은 블룸버그와 로이터의 수석 에디터를 합쳐놓은 듯한 초엘리트 경제 속보 분석가입니다.
    현재 수집된 뉴스 목록에서 '진짜 시장을 뒤흔들 파괴력 있는 속보'만 단 한두 개, 혹은 하나도 선택하지 않을 수 있습니다. 
    가볍고 흔한 소식은 과감히 버리세요.

    [후보 뉴스 리스트]
    {json.dumps(headlines, ensure_ascii=False)}

    [최근 보도된 속보 (중복 금지)]
    {json.dumps(recent_titles, ensure_ascii=False)}

    [엄격하되 유연한 필터링 기준]
    1. **필터링 대상 (Skip)**: 단순 시황 요약, 일반적인 증시 전망, 소형주 뉴스, 일상적인 홍보성 기사, 이미 알려진 정보의 단순 재탕.
    2. **우선 순위 (Must Include)**:
       - **핵심 지표**: CPI, PCE, 고용보고서, 금리 결정 등 주요 경제지표 공식 발표 즉시.
       - **시장 변동**: 환율 급등락, 국채 금리 폭등, 주요 지수(KOSPI, NASDAQ)의 유의미한 변동 및 추세 전환.
       - **기업 속보**: 삼성전자, SK하이닉스, 애플, 엔비디아 등 대장주들의 '기대치를 크게 벗어난' 실적 발표나 핵심 공시.
       - **정책/긴급**: 정부의 중대 시장 정책 발표, 금융권 긴급 수혈, 또는 실제 발생한 지정학적 충격.
    3. **무게감 판단**: '이 소식을 알게 됨으로써 투자자가 즉각적으로 행동을 고민하게 만드는가?'를 기준으로 삼으세요. 
    4. **중복 배제**: 이미 보도된 목록과 핵심 키워드가 겹치더라도, '새로운 수치가 발표'되었거나 '상황이 급진전'된 것이라면 포함하세요.

    [출력 형식]
    - 반드시 JSON 리스트 형식으로만 답변하세요. 
    - 기준에 부합하는 뉴스가 없으면 빈 리스트 []를 반환하세요.
    - 중요도(importance_score): 기사의 파급력에 따라 7~10점으로 부여하세요. (7점 미만은 누락)
    - title: 한국어로 15자 이내, 제목만 보고도 상황이 파악되게 명확하고 강렬하게. 문장 끝에 문장에 어울리는 이모지 하나 추가.
    - content: 수치나 핵심 팩트를 포함하여 1~2문장으로 압축.
    - category: 'market', 'indicator', 'geopolitics', 'corporate' 중 최적의 카테고리 선택.
    """

    try:
        response = model.generate_content(prompt)
        text = response.text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        
        candidates = json.loads(text.strip())
        return candidates
    except Exception as e:
        print(f"AI filtering error: {e}")
        return []

def save_and_notify(news_item):
    """
    DB에 저장하고 실시간 알림을 보냅니다.
    """
    try:
        # 안전한 키 참조 (KeyError 방지)
        title = news_item.get('title')
        content = news_item.get('content', '')
        score = news_item.get('importance_score', 7)
        category = news_item.get('category', 'market')
        url = news_item.get('original_url', '')

        if not title:
            return

        # 중복 체크
        res = supabase.table("breaking_news").select("id").eq("title", title).execute()
        if res.data:
            print(f"Skipping duplicate: {title}")
            return

        # 1. DB 저장 (이미지 추출 추가)
        image_url = None
        if url:
            try:
                config = Config()
                config.browser_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                config.request_timeout = 5
                article = Article(url, config=config)
                article.download()
                article.parse()
                image_url = article.top_image
            except Exception as e:
                print(f"Image fetch error: {e}")

        data = {
            "title": title,
            "content": content,
            "importance_score": score,
            "category": category,
            "original_url": url,
            "image_url": image_url
        }
        supabase.table("breaking_news").insert(data).execute()
        print(f"🚀 New Breaking News Saved: {title}")

        # 2. 실시간 푸시 알림 (전체 알림)
        send_push_to_all(
            title=f"[속보] {title}",
            body=content,
            url="/live" # 속보 타임라인 전용 페이지로 링크
        )
    except Exception as e:
        print(f"Error in save_and_notify: {e}")

def main():
    print("🎬 24/7 Breaking News Tracker is running...")
    
    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] Monitoring for updates...")
            
            # 1. 헤드라인 수집
            raw_headlines = fetch_latest_headlines()
            
            # 2. 중복 필터링 (메모리 기반)
            new_headlines = []
            for h in raw_headlines:
                if h['title'] not in processed_news:
                    new_headlines.append(h)
                    processed_news.add(h['title'])
            
            # 메모리 관리 (최근 500개만 유지)
            if len(processed_news) > 500:
                processed_news.clear()

            # 3. DB에서 최근 보도된 뉴스 목록 가져오기 (문맥 파악 및 중복 방지용)
            recent_titles = get_recent_news_titles()

            # 4. AI 필터링 및 요약 (최근 보도 목록 전달)
            if new_headlines:
                print(f"🔍 Analyzing {len(new_headlines)} new headlines with AI...")
                breaking_items = filter_breaking_news(new_headlines, recent_titles)
                
                if not breaking_items:
                    print("🍃 No high-impact breaking news found in this batch.")
                
                # 5. 저장 및 알림
                for item in breaking_items:
                    save_and_notify(item)
            else:
                print("💤 No new headlines to analyze.")
            
            # 6. 주기 설정 (120초 - 2분마다 체크)
            # 유동적으로 조절 가능
            time.sleep(5)
            
        except KeyboardInterrupt:
            print("Tracker stopped by user.")
            break
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
