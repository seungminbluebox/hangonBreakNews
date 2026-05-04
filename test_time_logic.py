import feedparser
import calendar
from datetime import datetime, timedelta, timezone

# 테스트할 RSS 피드 (현재 사용 중인 주요 소스)
TEST_FEEDS = [
    # # 1. Bloomberg (via Google News) - 블룸버그 1시간 내 속보 우회 수집
    # "https://news.google.com/rss/search?q=site:bloomberg.com+when:1h&hl=en-US&gl=US&ceid=US:en",

    # # 2. CNBC Top & Breaking News (미장 시작 전후 실적발표 및 M&A 최적화)
    # "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",

    # # 3. ForexLive (외환시장, 주요국 중앙은행 인사들의 실시간 발언, 거시경제 단신이 가장 빠름)
    # "https://www.forexlive.com/feed/news",

    # # 4. FXStreet 실시간 경제 뉴스 (Trading Economics의 403 차단을 완전히 대체하는 가장 빠른 거시경제/외환 단신 매체)
    # "https://www.fxstreet.com/rss",
    
    # # 5. Investing.com Headlines / Top News (주요 헤드라인 전용 RSS)
    # "https://www.investing.com/rss/news_25.rss",
    
    # # 6. CoinDesk (주말/새벽 암호화폐 및 거시경제 선행지표 최적화)
    # "https://www.coindesk.com/arc/outboundfeeds/rss/",

    # # 7. TheStreet (미국 주식 개별 종목, 단독 특징주 및 시장 모멘텀 보완)
    # "https://www.thestreet.com/.rss/full/",
    
    # # 8. Cointelegraph (가장 빠르고 굵직한 글로벌 암호화폐 전용 실시간 속보 매체)
    # "https://cointelegraph.com/rss",
    #     # 9. Benzinga (미국 주식 실시간 특징주 및 루머/단신 최적화)
    # "https://www.benzinga.com/feed",
    
    # # 10. Yahoo Finance (로이터, 블룸버그 등 통신사 종합 실시간 송고)
    # "https://finance.yahoo.com/news/rssindex",
    
    # # 11. Seeking Alpha Market Currents (개별 기업 공시, 배당, 실적 등 미시적 팩트)
    # "https://seekingalpha.com/market_currents.xml",
    #     # 12. ZeroHedge (월가 실시간 루머, 긴급 지정학적/거시경제 속보가 가장 빠르고 날것으로 올라옴)
    # "https://feeds.feedburner.com/zerohedge/feed",
    
    # # 13. Financial Times Markets (정통 거시경제, M&A, 글로벌 탑티어 팩트 보도)
    # "https://www.ft.com/markets?format=rss",
    
    # 14. NYT Business (미국 정부/기업 규제, 소송, 초대형 기업 소식 모니터링)
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",

    # 15. Defense News (글로벌 방위산업 및 군사 전략)
    "https://www.defensenews.com/arc/outboundfeeds/rss/",

    # 16. Breaking Defense (국방 정책, 군사 기술, 무기 체계)
    "https://breakingdefense.com/feed/",

    # 17. Al Jazeera (중동 분쟁 및 글로벌 속보)
    "https://www.aljazeera.com/xml/rss/all.xml",


    # 19. BBC News - World (국제 뉴스 전반)
    "http://feeds.bbci.co.uk/news/world/rss.xml"
]

def test_time_logic():
    print("=== 🕒 RSS 시차 보정 및 30분 로직 테스트 ===\n")
    
    # 1. 기준 시간 설정 (현재 시스템의 절대 UTC)
    now_utc = datetime.now(timezone.utc)
    time_limit_utc = now_utc - timedelta(minutes=180)
    
    print(f"현재 시스템 시각 (UTC): {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"속보 판단 커트라인 (UTC): {time_limit_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 50)

    custom_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

    for url in TEST_FEEDS:
        print(f"\n📡 피드 확인 중: {url}")
        try:
            feed = feedparser.parse(url, agent=custom_agent)
            if not feed.entries:
                print("   ⚠️ 검색된 기사가 없습니다.")
                continue

            for entry in feed.entries[:3]: # 최신 3개만 확인
                title = entry.title[:50]
                
                # 기사 시간 파싱 (UTC로 변환)
                pub_datetime_utc = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_ts = calendar.timegm(entry.published_parsed)
                    pub_datetime_utc = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                
                if pub_datetime_utc:
                    age_diff = now_utc - pub_datetime_utc
                    is_recent = pub_datetime_utc >= time_limit_utc
                    
                    status = "✅ [합격] 30분 이내" if is_recent else "❌ [탈락] 30분 초과"
                    
                    print(f"   - 제목: {title}...")
                    print(f"     발행시각(UTC): {pub_datetime_utc.strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"     시각 차이: {age_diff}")
                    print(f"     판단 결과: {status}")
                else:
                    print(f"   - 제목: {title}...")
                    print("     ⚠️ 날짜 정보를 파싱할 수 없습니다.")
        except Exception as e:
            print(f"   🚨 에러 발생: {e}")

if __name__ == "__main__":
    test_time_logic()
