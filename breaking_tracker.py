import os
import sys
import time
import json
import calendar
import feedparser
import requests
from collections import deque
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from newspaper import Article, Config
import google.generativeai as genai

# 상위 디렉토리 참조 추가
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from push_notification import send_push_notification
from revalidate import revalidate_path

import random

load_dotenv()

# 환경 변수 및 설정
GEMINI_MODEL_NAME= os.getenv("GEMINI_MODEL_NAME", "gemini-3.1-flash-lite-preview")
API_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3")
]
# None 값이 섞이는 것을 방지
API_KEYS = [k for k in API_KEYS if k]

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

import random

# 라운드 로빈(순차 교환) API 키 사용을 위한 전역 상태 변수
current_api_key_idx = 0

def safe_generate_content(prompt_text, max_retries=5):
    global current_api_key_idx
    """429 에러 및 한도 문제 방어를 위해 여러 API 키를 번갈아 사용하는 함수"""
    for attempt in range(max_retries):
        # 매 시도마다 사용할 API 키 순차 선택 (1->2->3->1...)
        current_key = API_KEYS[current_api_key_idx]
        current_api_key_idx = (current_api_key_idx + 1) % len(API_KEYS)
        
        genai.configure(api_key=current_key)
        
        # 모델 생성을 시도할 때마다 새로 인스턴스화
        temp_model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        
        try:
            return temp_model.generate_content(prompt_text)
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "Too Many" in error_msg or "Quota" in error_msg or "Resource" in error_msg:
                wait_time = (attempt + 1) * 5
                print(f"⚠️ [429 Error/Rate Limit] 키 번갈아가며 {wait_time}초 대기 후 API 재시도 중... (시도 {attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise e
    print("🚨 최대 재시도 횟수를 초과했습니다. 모든 API 키가 한도 초과 상태일 수 있습니다.")
    return None

# 감시할 뉴스 소스 (RSS) - 실시간 '속보' 전용 시스템으로 전면 교체
RSS_FEEDS = [
    # 1. Reuters (via Google News) - 로이터 통신 (최근 1시간 내 구글에 인덱싱된 로이터 실시간 기사 우회 수집)
    "https://news.google.com/rss/search?q=site:reuters.com+when:1h&hl=en-US&gl=US&ceid=US:en",
    
    # 2. Bloomberg (via Google News) - 블룸버그 1시간 내 속보 우회 수집
    "https://news.google.com/rss/search?q=site:bloomberg.com+when:1h&hl=en-US&gl=US&ceid=US:en",
    
    # 3. WSJ (via Google News) - 월스트리트저널 1시간 내 속보 우회 수집
    "https://news.google.com/rss/search?q=site:wsj.com+when:1h&hl=en-US&gl=US&ceid=US:en",

    # 4. MarketWatch MarketPulse (단신/수치 팩트 최강, 해설 기사가 거의 없고 수치 위주의 가장 빠른 매체)
    "http://feeds.marketwatch.com/marketwatch/marketpulse/",

    # 5. CNBC Top & Breaking News (미장 시작 전후 실적발표 및 M&A 최적화)
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",

    # 6. ForexLive (외환시장, 주요국 중앙은행 인사들의 실시간 발언, 거시경제 단신이 가장 빠름)
    "https://www.forexlive.com/feed/news",

    # 7. FXStreet 실시간 경제 뉴스 (Trading Economics의 403 차단을 완전히 대체하는 가장 빠른 거시경제/외환 단신 매체)
    "https://www.fxstreet.com/rss",

    # 8. Investing.com Breaking News (순수 속보 채널)
    "https://www.investing.com/rss/news_285.rss",
    
    # 9. Investing.com Headlines / Top News (주요 헤드라인 전용 RSS)
    "https://www.investing.com/rss/news_25.rss",
    
    # 10. CoinDesk (주말/새벽 암호화폐 및 거시경제 선행지표 최적화)
    "https://www.coindesk.com/arc/outboundfeeds/rss/",

    # 11. TheStreet (미국 주식 개별 종목, 단독 특징주 및 시장 모멘텀 보완)
    "https://www.thestreet.com/.rss/full/",
    
    # 12. Cointelegraph (가장 빠르고 굵직한 글로벌 암호화폐 전용 실시간 속보 매체)
    "https://cointelegraph.com/rss"
]

# 메모리 상에서 이미 처리한 뉴스 제목 저장 (중복 방지 및 메모리 효율화)
processed_news = deque(maxlen=500)


def is_already_saved(url):
    """DB에 이미 해당 URL의 속보가 있는지 확인합니다."""
    try:
        res = supabase.table("breaking_news").select("id").eq("original_url", url).execute()
        return len(res.data) > 0
    except Exception as e:
        print(f"Error checking duplicate in DB: {e}")
        return False


def get_recent_news_titles():
    """DB에서 최근 50개의 속보 제목을 가져옵니다."""
    try:
        res = supabase.table("breaking_news").select("title").order("created_at", desc=True).limit(50).execute()
        return [item['title'] for item in res.data]
    except Exception as e:
        print(f"Error fetching recent titles: {e}")
        return []

def fetch_latest_headlines():
    headlines = []
    # 1. 기준 시간 설정 (시차 지연 및 언론사 RSS 반영 지연 대비: 3시간(180분)으로 여유 있게 설정)
    # 진짜 필터링은 DB 중복 체크 + AI 문맥 파악이 담당하므로 시간은 넉넉하게 잡는 것이 안전함.
    now_utc = datetime.now(timezone.utc)
    time_limit_utc = now_utc - timedelta(minutes=30)
    
    # ❌ 명백한 '해설/요약/전망' 기사는 AI 토큰 낭비를 막기 위해 1차 블랙리스트로만 걸러냅니다.
    # 기존의 딱딱한 BREAKING_KEYWORDS, MARKET_INDICATORS는 모두 삭제합니다. (AI 문맥 파악으로 전면 교체)
    EXCLUDE_KEYWORDS = ["전망", "동향", "분석", "마감", "주목할", "이유는", "요약", "정리", "wrap", "recap", "preview", "takeaways", "opinion", "why", "snapshot", "roundup", "should you buy", "what to watch", "칼럼", "포인트"]
    
    custom_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    
    # 2. RSS 피드 수집 (Global/Google 속보 피드)
    for i, url in enumerate(RSS_FEEDS, 1):
        try:
            feed = feedparser.parse(url, agent=custom_agent)
            entries_found = len(feed.entries)
            print(f"📡 Source {i} (RSS) checking: {entries_found} entries found.")
            
            # [디버깅용] 각 소스의 가장 최신 기사 1개의 제목과 발행 시간 출력
            if entries_found > 0:
                first_entry = feed.entries[0]
                first_pub = "No Date"
                if hasattr(first_entry, 'published'):
                    first_pub = first_entry.published
                print(f"   🔝 Latest in Feed: [{first_pub}] {first_entry.title[:60]}...")

            for entry in feed.entries:
                title_lower = entry.title.lower()
                
                # [블랙리스트 필터] 해설/칼럼은 스킵
                if any(ex_kw in title_lower for ex_kw in EXCLUDE_KEYWORDS):
                    continue

                # ✅ [혁신 포인트] 기존의 '단어 기반 입구 컷' 삭제!
                # "Breaking"이라는 단어가 없어도, 내용 자체가 충격적인 사건일 수 있으므로 
                # 시간에만 맞으면 전부 AI에게 넘겨서 '문맥'으로 판단하게 만듭니다.

                pub_datetime_utc = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_ts = calendar.timegm(entry.published_parsed)
                    pub_datetime_utc = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                
                is_recent = False
                if pub_datetime_utc:
                    if pub_datetime_utc >= time_limit_utc:
                        is_recent = True
                    else:
                        print(f"  ❌ Skip (Too Old): {entry.title[:50]}...")
                else:
                    is_recent = True
                
                if is_recent:
                    print(f"  ✅ Candidate (RSS): {entry.title[:50]}...")
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

                # [블랙리스트 필터] 해설성 기사, 동향, 요약 기사는 무조건 스킵
                if any(ex_kw in title_lower for ex_kw in EXCLUDE_KEYWORDS):
                    continue

                # [필터 1] 네이버 뉴스: 기존 하드코딩 키워드 삭제 -> 무조건 통과시켜서 AI가 '문맥'으로 판단하게 함
                # (단, 앞선 EXCLUDE_KEYWORDS에 해당하는 해설/칼럼은 이미 위에서 걸러짐)

                base_link = "https://finance.naver.com" + subject_tag['href']
                # 네이버 뉴스 링크를 PC/모바일 통합 링크(n.news.naver.com)로 변환 (모바일 접근성 및 PC 가용성 동시 해결)
                link = base_link
                try:
                    parsed = urlparse(base_link)
                    params = parse_qs(parsed.query)
                    aid = params.get('article_id', [None])[0]
                    oid = params.get('office_id', [None])[0]
                    if aid and oid:
                        link = f"https://n.news.naver.com/mnews/article/{oid}/{aid}"
                except:
                    pass

                date_str = wdate_tag.text.strip().replace(".", "-")
                
                try:
                    pub_time_kst = datetime.strptime(date_str, "%Y-%m-%d %H:%M").replace(tzinfo=kst)
                    pub_time_utc = pub_time_kst.astimezone(timezone.utc)
                    
                    if pub_time_utc >= time_limit_utc:
                        print(f"  ✅ Candidate (Naver): {title[:50]}...")
                        headlines.append({
                            "title": title,
                            "link": link,
                            "source": "Naver Finance (Strict)"
                        })
                    else:
                        print(f"  ❌ Skip (Too Old): {title[:50]}...")
                except: pass
    except Exception as e:
        print(f"Error fetching Naver breaking news: {e}")

    return headlines

def filter_breaking_news(headlines, recent_titles):
    """
    Gemini AI를 사용하여 수집된 뉴스 중 진짜 '속보' 가치가 있는 것만 선별합니다.
    최근에 이미 보도된 내용과 겹치는지 체크합니다.
    URL 변조를 방지하기 위해 ID 매핑 방식을 사용합니다.
    """
    if not headlines:
        return []

    # 원본 URL 보존을 위한 ID 매핑
    headlines_with_id = []
    for idx, h in enumerate(headlines):
        h_copy = h.copy()
        h_copy['temp_id'] = idx
        headlines_with_id.append(h_copy)

    prompt = f"""
    당신은 글로벌 경제 및 증시 트렌드를 발빠르게 전달하는 수석 에디터입니다.
    현재 수집된 뉴스 목록에서 '시장의 흐름을 파악하는 데 도움이 되는 유의미한 뉴스'들을 선별해주세요.
    
    [후보 뉴스 리스트]
    {json.dumps(headlines_with_id, ensure_ascii=False)}

    [최근 보도된 속보 (중복 금지)]
    {json.dumps(recent_titles, ensure_ascii=False)}

    [엄격한 '단일 사건' 선별 기준 - 동향 분석 절대 금지]
    아무리 30분 이내에 올라온 기사라도, 이미 일어난 일을 설명하거나 풀이하는 '해설 기사'는 철저하게 걸러내야 합니다. 오직 방금 발생한 '새로운 팩트(New Fact)'가 발생한 기사만 선별하세요.
    0. **중복 완벽 차단 (최우선)**: 위에 제공된 [최근 보도된 속보] 목록을 반드시 읽으세요. 다른 언론사가 썼거나 제목이 달라도, **이미 보도된 속보와 '동일한 사건(원인/결과)'**이라면 절대 중복해서 내보내지 말고 **무조건 버리세요.**
    1. **필터링 대상 (무조건 Skip)**: 
       - "증시 마감 요약", "주간 동향", "오늘의 시장 정리(Wrap-up)", "경제 지표 프리뷰(Preview)"
       - "테슬라 주가가 급락한 3가지 이유", "향후 전망과 분석(Opinion/Analysis)", "전문가 칼럼" 등.
    2. **적극 포함 대상 (Include - 사건/지표 중심)**:
       - 막 발표된 경제 지표 결과치 (예: "미국 1월 CPI 3.1% 발표")
       - 실시간 금리 발표, 기업의 실적 발표, 기업 인수합병(M&A), 깜짝 수주 및 공시
       - 전쟁 발발, 대규모 군사 타격(공습, 미사일), 주요국 지도자의 긴급 발표 등 시장(유가/증시)에 즉각적 충격을 주는 **초긴급 지정학적 속보 (최우선 포함)**
       - 제목에 'Breaking', 'Urgent', '단독', '속보'가 포함된 명백한 "신규 발생 사건"
    3. **무게감 판단**: '이 기사는 기자가 자기 의견을 쓴 것인가(X), 아니면 방금 세계 어딘가에서 새로운 데이터나 결과, 물리적 타격(이벤트)이 발생했는가(O)?'를 기준으로 삼으세요. 

    [출력 형식]
    - 반드시 JSON 리스트 형식으로만 답변하세요. 
    - 해설성/요약성/전망 기사는 하나도 빠짐없이 전부 버리고([] 반환), 명백한 '단독(Exclusive)', '긴급 속보(Urgent/Breaking)'(전쟁/테러 포함), '주요 지표/실적 발표'에 해당하는 경우에만 JSON 객체를 만드세요.
    - 중요도(importance_score): 기사의 파급력에 따라 1~10점으로 부여하되, '오직 새로운 사건이자 당장 알아야 할 긴급 팩트'인 경우에만 7점 이상을 주어 엄격하게 승인하세요.
    - temp_id: [후보 뉴스 리스트]에서 해당 뉴스의 temp_id를 그대로 가져오세요.
    - title: 한국어로 15자 이내, 제목만 보고도 상황이 파악되게 명확하게. 문장 끝에 문장에 어울리는 이모지 하나 추가.
    - content: 수치나 핵심 팩트를 포함하여 1~2문장으로 압축.
    - category: 'market', 'indicator', 'geopolitics', 'corporate' 중 최적의 카테고리 선택.
    """

    try:
        response = safe_generate_content(prompt)
        if not response:
            return []
        text = response.text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        
        candidates = json.loads(text.strip())
        
        # URL 복원 및 결과 검증
        valid_candidates = []
        candidate_ids = set()
        for c in candidates:
            t_id = c.get('temp_id')
            if t_id is not None and 0 <= t_id < len(headlines):
                # 원본 URL을 코드가 직접 유지한 데이터에서 매핑 (AI 변조 방지)
                c['original_url'] = headlines[t_id]['link']
                valid_candidates.append(c)
                candidate_ids.add(t_id)
        
        # 필터링 결과 로그 출력
        for h_id, h in enumerate(headlines):
            if h_id not in candidate_ids:
                print(f"  🗑️ AI Rejected: {h['title'][:50]}...")
            else:
                print(f"  💎 AI Selected: {h['title'][:50]}...")

        return valid_candidates
    except Exception as e:
        print(f"AI filtering error: {e}")
        return []

def perform_deep_analysis(candidates):
    """
    선별된 뉴스 후보들의 본문을 직접 읽고, 
    수치 검증 및 상세 분석을 통해 최종 뉴스 데이터를 생성합니다.
    """
    refined_items = []
    
    for item in candidates:
        url = item.get('original_url')
        if not url:
            continue
            
        try:
            # 1. 본문 추출
            config = Config()
            config.browser_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            config.request_timeout = 10
            article = Article(url, config=config)
            article.download()
            article.parse()
            
            full_text = article.text
            top_image = article.top_image
            
            if len(full_text) < 100: # 본문이 너무 적으면 패스하거나 제목 기반 유지
                print(f"⚠️ Text too short for {item['title']}, generating rich content from title.")
                # 본문이 짧을 경우 제목을 바탕으로 상세 내용을 추론하여 생성하도록 프롬프트 강화
                fallback_prompt = f"""
                당신은 경제 전문 에디터입니다. 다음의 '짧은 속보 제목'을 바탕으로, 
                독자가 상황을 충분히 파악할 수 있도록 팩트 중심의 상세 문장(50자~100자 사이)을 생성해주세요.
                
                [기사 제목]: {item['title']}
                
                [지시사항]
                - 제목에 담긴 핵심 사건(유가 변동, 증시 출발 등)을 풀어서 설명하세요.
                - 절대 '본문이 없다'는 식의 무책임한 말은 하지 마세요.
                - 반드시 30자 이상의 완성된 문장으로 작성하세요.
                - 블룸버그/로이터 뉴스 톤앤매너를 유지하세요.
                """
                try:
                    fb_response = safe_generate_content(fallback_prompt)
                    if fb_response:
                        item['content'] = fb_response.text.strip()
                    else:
                        item['content'] = f"{item['title']} 관련 긴급 시장 상황이 발생했습니다. 자세한 내용은 원본 기사를 참조하시기 바랍니다."
                except:
                    item['content'] = f"{item['title']} 관련 긴급 시장 상황이 발생했습니다. 자세한 내용은 원본 기사를 참조하시기 바랍니다."
                
                item['image_url'] = top_image
                refined_items.append(item)
                
                # RPM 분산을 위한 쿨타임 주입 (1분 15회 제한 고려)
                time.sleep(4)
                continue

            # 2. 본문 기반 AI 재심사 및 요약
            prompt = f"""
            당신은 세계 최고의 경제 전문 팩트체커입니다. 
            다음 기사의 본문을 읽고, 제목에서 파악되지 않은 '구체적인 수치'와 '핵심 맥락'을 포함하여 내용을 정교하게 다듬어주세요.

            [기사 제목]: {item['title']}
            [기사 본문]: {full_text[:3000]} 

            [작성 가이드라인]
            - **수치 및 팩트 강조**: 경제 지표/실적 기사인 경우 퍼센트(%), 금액($), 포인트(p/bp) 등 수치를 반드시 포함하세요. 단, **전쟁, 공습, 테러, 지정학적 충돌** 같은 사건의 경우 수치 대신 타격 위치, 사상자, 지도자의 발언 등 **결정적인 '물리적 팩트'**를 명시하세요.
            - **정확도**: 기사 내용이 제목과 완전히 다르거나 무관한 낚시성 기사일 경우에만 과감히 빈 객체를 반환하세요. 진짜 발생한 속보/사건이라면 **절대 빈 객체를 반환하지 마세요.**
            - **품격**: 블룸버그/로이터 속보 톤앤매너를 유지하세요.

            [출력 형식 (JSON 하나만 출력)]
            {{
                "title": "한국어 15자 이내 (이모지 포함)",
                "content": "본문의 핵심 수치가 포함된 1~2문장 요약(110자 이내)",
                "importance_score": 7~10점 사이 점수,
                "category": "market/indicator/geopolitics/corporate 중 선택"
            }}
            """
            
            response = safe_generate_content(prompt)
            if not response:
                # API 호출 완전 실패 시 해당 기사는 다음 기회를 위해 스킵하거나 기본값 처리
                time.sleep(4)
                continue
                
            text = response.text
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            
            refined_data = json.loads(text.strip())
            if refined_data and refined_data.get('title'):
                # 원본 URL을 AI 출력물이 아닌, 기존 코드에서 유지하던 데이터로 강제 할당 (보보 안전성)
                refined_data['original_url'] = url
                refined_data['image_url'] = top_image
                refined_items.append(refined_data)
                print(f"  ✨ Deep Analysis Success: {refined_data['title']}")
            else:
                print(f"  🗑️ Deep Analysis Rejected (AI): {item['title'][:50]}...")
                
        except Exception as e:
            print(f"Deep analysis error for {url}: {e}")
            # 에러 발생 시 1차 분석 결과라도 유지
            refined_items.append(item)
            
        # RPM 분산을 위한 쿨타임 강제 주입 (다음 뉴스 처리 전 최소 4초 대기)
        time.sleep(4)
            
    return refined_items

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
        top_image_url = news_item.get('image_url')

        if not title or score < 7:
            if title:
                print(f"  Filtered out by score ({score}): {title[:50]}...")
            return

        # 중복 체크 (DB 최종 확인)
        if is_already_saved(url):
            print(f"Skipping duplicate: {title}")
            return

        # 1. DB 저장
        data = {
            "title": title,
            "content": content,
            "importance_score": score,
            "category": category,
            "original_url": url
        }
        
        supabase.table("breaking_news").insert(data).execute()
        print(f"🚀 New Breaking News Saved: {title} (Score: {score})")
        
        # On-Demand Revalidation
        revalidate_path("/live")
        revalidate_path("/") # 메인 페이지 마켓 티커 등 업데이트용

        # 2. 실시간 푸시 알림 (중요도에 따른 카테고리 분기)
        # score >= 8: 중요 속보 (important_breaking_news)
        # score < 8: 일반 속보 (breaking_news)
        notification_category = "important_breaking_news" if score >= 8 else "breaking_news"
        
        # 중요도에 따른 접두어 및 강조
        prefix = "[속보]"
        if score >= 9:
            prefix = "🚨[초긴급]"
        elif score >= 8:
            prefix = "[속보]"
        
        send_push_notification(
            title=f"{prefix} {title}",
            body=content,
            url="/live", # 속보 타임라인 전용 페이지로 링크
            category=notification_category
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
            
            # 2. 필터링: 메모리 중복 + DB 중복 동시 체크 (AI 비용 절감)
            new_headlines = []
            for h in raw_headlines:
                title = h['title']
                link = h.get('link', '')
                if link not in processed_news:
                    if not is_already_saved(link):
                        new_headlines.append(h)
                    else:
                        print(f"  ⏭️ Skip (Already in DB): {title[:50]}...")
                    processed_news.append(link)  # deque는 자동으로 오래된 요소 제거
                else:
                    # print(f"  ⏭️ Skip (Memory): {title[:50]}...")
                    pass
            
            # 3. DB에서 최근 보도된 뉴스 목록 가져오기 (문맥 파악 및 중복 방지용)
            recent_titles = get_recent_news_titles()

            # 4. AI 필터링 및 요약
            if new_headlines:
                # [1차] 제목 기반 후보 선별
                print(f"🔍 [Pass 1] Screening {len(new_headlines)} headlines...")
                candidates = filter_breaking_news(new_headlines, recent_titles)
                
                if candidates:
                    # [2차] 본문 데이터 추출 및 심층 분석
                    print(f"🧐 [Pass 2] Deep analyzing {len(candidates)} candidates...")
                    final_items = perform_deep_analysis(candidates)
                    
                    # 5. 저장 및 알림
                    for item in final_items:
                        save_and_notify(item)
                else:
                    print("🍃 No high-impact candidates found by titles.")
            else:
                print("💤 No new headlines to analyze.")
            
            # 6. 주기 설정 (120초 - 2분마다 체크 추천, 현재는 180초)
            time.sleep(120)
            
        except KeyboardInterrupt:
            print("Tracker stopped by user.")
            break
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
