# 스크립트 최상단에 전역 변수로 추가
processed_reviews_in_session = set()

import os
import json
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import hashlib
import re
import unicodedata
from datetime import datetime, timedelta
from selenium.webdriver.common.action_chains import ActionChains
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException
)
import openai
from dotenv import load_dotenv
from supabase import create_client, Client
import calendar
# 로그 파일 설정 코드 추가 - 여기에 배치
import sys
from datetime import datetime
import io

# 로그 파일 설정
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = os.path.join(log_dir, f"baemin_review_{current_time}.log")

# 로그 파일 준비 (실행 종료 시 저장되도록 변경)
log_buffer = io.StringIO()

# 터미널과 버퍼에 동시에 출력하는 클래스
class TeeOutput:
    def __init__(self, original_stdout, buffer):
        self.original_stdout = original_stdout
        self.buffer = buffer

    def write(self, message):
        self.original_stdout.write(message)
        self.buffer.write(message)

    def flush(self):
        self.original_stdout.flush()
        self.buffer.flush()

# stdout와 stderr를 TeeOutput 객체로 교체
sys.stdout = TeeOutput(sys.__stdout__, log_buffer)
sys.stderr = TeeOutput(sys.__stderr__, log_buffer)

# 프로그램 종료 시 로그 파일 저장 함수
def save_log_on_exit():
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(log_buffer.getvalue())
    print(f"로그가 {log_file}에 저장되었습니다.")

# 환경 설정
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

CONFIG_FILE = 'config.json'

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = openai

###################################################
# 1) 리뷰 해시 생성 및 ID 처리 함수들
###################################################
def generate_review_hash(store_code: str, author: str, review_text: str) -> str:
    """
    store_code + author(닉네임) + review_text를 합쳐 md5 해시를 생성.
    날짜(오늘/어제 등)는 제외하여 매번 달라지지 않도록 한다.
    """
    base_str = f"{store_code}_{author}_{review_text}"
    hash_val = hashlib.md5(base_str.encode("utf-8")).hexdigest()
    print(f"[generate_review_hash] base_str={base_str} => hash={hash_val[:8]}...")
    return hash_val

def extract_date_from_review_id(review_id):
    """리뷰 ID에서 날짜를 추출하여 YYYY-MM-DD 형식으로 반환"""
    try:
        id_str = str(review_id)
        
        # 다양한 길이와 형식의 ID에 대응
        if len(id_str) < 8:
            return datetime.now().date().strftime("%Y-%m-%d")
        
        # 앞 8자리 또는 적절한 위치의 날짜 정보 추출
        year = id_str[:4]
        month = id_str[4:6]
        day = id_str[6:8]
        
        # 유효성 검사 (연도는 2000년 이후, 월/일은 유효 범위)
        current_year = datetime.now().year
        if (2000 <= int(year) <= current_year and 
            1 <= int(month) <= 12 and 
            1 <= int(day) <= 31):
            return f"{year}-{month}-{day}"
        
        return datetime.now().date().strftime("%Y-%m-%d")
    except Exception as e:
        print(f"[extract_date_from_review_id] 날짜 추출 오류: {str(e)}, ID: {review_id}")
        return datetime.now().date().strftime("%Y-%m-%d")

# 여기에 새 함수 추가
def extract_relative_date(driver, card_element):
    """새로운 HTML 구조에 맞게 상대적 날짜 추출 함수 수정"""
    try:
        # 날짜 요소 찾기 - 클래스 속성과 구조를 더 정확히 지정
        date_selectors = [
            # 1. 명확한 Typography 클래스 속성을 가진 날짜 요소
            ".//span[contains(@class, 'Typography_b_qmgb_1bisyd4b') and contains(@class, 'Typography_b_qmgb_1bisyd41v')]",
            # 2. 일반적인 날짜 관련 텍스트 찾기 (패턴 확장)
            ".//span[contains(text(), '오늘') or contains(text(), '어제') or contains(text(), '그제') or contains(text(), '일 전') or contains(text(), '지난 달') or contains(text(), '지난 주') or contains(text(), '이번 달') or contains(text(), '이번 주')]"
        ]
        
        relative_date = None
        for selector in date_selectors:
            try:
                # 여러 요소가 일치할 수 있으므로 모두 찾기
                date_elements = card_element.find_elements(By.XPATH, selector)
                for date_el in date_elements:
                    text = date_el.text.strip()
                    # 날짜 관련 키워드 확인 (키워드 확장)
                    if text and (text in ["오늘", "어제", "그제", "지난 달", "지난 주", "이번 달", "이번 주"] or 
                                "전" in text or "월" in text or "일" in text):
                        relative_date = text
                        print(f"[extract_relative_date] 날짜 요소 찾음: '{relative_date}'")
                        break
                if relative_date:
                    break
            except Exception as e:
                print(f"[extract_relative_date] 선택자 '{selector}' 시도 중 오류: {str(e)}")
                continue
                
        # 날짜를 찾지 못한 경우 추가 시도
        if not relative_date:
            # 모든 span 요소에서 검색 (패턴 확장)
            try:
                spans = card_element.find_elements(By.XPATH, ".//span")
                for span in spans:
                    text = span.text.strip()
                    if (text in ["오늘", "어제", "그제", "지난 달", "지난 주", "이번 달", "이번 주"] or 
                        "전" in text or "월" in text or "일" in text):
                        relative_date = text
                        print(f"[extract_relative_date] 추가 검색으로 날짜 찾음: '{relative_date}'")
                        break
            except:
                pass
        
        # 날짜를 찾지 못한 경우 기본값 사용
        if not relative_date:
            print("[extract_relative_date] 날짜 요소를 찾을 수 없음, 현재 날짜 사용")
            return datetime.now().date().isoformat()
        
        today = datetime.now().date()
        
        # 상대적 날짜를 실제 날짜로 변환 (새로운 패턴 추가)
        if relative_date == "오늘":
            return today.isoformat()
        elif relative_date == "어제":
            return (today - timedelta(days=1)).isoformat()
        elif relative_date == "그제" or relative_date == "2일 전":
            print("[extract_relative_date] '그제' 또는 '2일 전' 인식 -> 2일 전 날짜 반환")
            return (today - timedelta(days=2)).isoformat()
        elif relative_date == "3일 전":
            return (today - timedelta(days=3)).isoformat()
        # 새로운 패턴 처리 추가
        elif "지난 달" in relative_date or "저번 달" in relative_date:
            # 한 달 전으로 이동
            print("[extract_relative_date] '지난 달' 인식 -> 한 달 전 날짜 반환")
            last_month = today.replace(day=1) - timedelta(days=1)
            # 현재 일자와 동일한 날짜로 설정 (해당 월의 최대 일수 고려)
            return last_month.replace(day=min(today.day, calendar.monthrange(last_month.year, last_month.month)[1])).isoformat()
        elif "지난 주" in relative_date or "저번 주" in relative_date:
            # 일주일 전으로 이동
            print("[extract_relative_date] '지난 주' 인식 -> 7일 전 날짜 반환")
            return (today - timedelta(days=7)).isoformat()
        elif "이번 달" in relative_date:
            # 이번 달은 10일 전으로 계산
            print("[extract_relative_date] '이번 달' 인식 -> 10일 전 날짜 반환")
            return (today - timedelta(days=10)).isoformat()
        elif "이번 주" in relative_date:
            # 이번 주는 4일 전으로 계산
            print("[extract_relative_date] '이번 주' 인식 -> 4일 전 날짜 반환")
            return (today - timedelta(days=4)).isoformat()
        elif "주 전" in relative_date:
            # n주 전 패턴 처리
            try:
                weeks = int(relative_date.split("주 전")[0].strip())
                print(f"[extract_relative_date] '{weeks}주 전' 인식 -> {weeks*7}일 전 날짜 반환")
                return (today - timedelta(days=weeks * 7)).isoformat()
            except:
                print(f"[extract_relative_date] '주 전' 패턴 처리 실패: '{relative_date}'")
        elif "개월 전" in relative_date or "달 전" in relative_date:
            # n개월 전 패턴 처리
            try:
                if "개월 전" in relative_date:
                    months = int(relative_date.split("개월 전")[0].strip())
                else:
                    months = int(relative_date.split("달 전")[0].strip())
                    
                print(f"[extract_relative_date] '{months}개월 전' 인식")
                
                # n개월 전 날짜 계산
                result_date = today
                for _ in range(months):
                    # 한 달씩 뒤로 이동
                    result_date = result_date.replace(day=1) - timedelta(days=1)
                    
                # 현재 일자와 동일한 날짜로 설정 (해당 월의 최대 일수 고려)
                result_date = result_date.replace(
                    day=min(today.day, calendar.monthrange(result_date.year, result_date.month)[1])
                )
                return result_date.isoformat()
            except Exception as e:
                print(f"[extract_relative_date] '개월 전' 패턴 처리 실패: '{relative_date}', 오류: {str(e)}")
        
        # 다른 모든 방법이 실패하면 현재 날짜 반환
        print(f"[extract_relative_date] 날짜 형식 인식 실패: '{relative_date}', 현재 날짜로 대체")
        return today.isoformat()
        
    except Exception as e:
        print(f"[extract_relative_date] 날짜 추출 중 오류: {str(e)}")
        return datetime.now().date().isoformat()

def get_review_identifier(card_element, store_code):
    """카드에서 고유한 식별자 추출"""
    try:
        # 작성자
        author = card_element.find_element(By.CSS_SELECTOR, 'p.nick').text.strip()
        
        # 리뷰 내용
        try:
            review_text = card_element.find_element(By.CSS_SELECTOR, 'p.review-cont').text.strip()
        except:
            review_text = ""
            
        # 별점 (추가 식별자)
        try:
            rating_els = card_element.find_elements(
                By.CSS_SELECTOR, 'div.rating-stars > svg > path[fill="#FFC600"]'
            )
            rating = len(rating_els)
        except:
            rating = 0
            
        # 해시 생성 (store_code + author + review_text + rating)
        identifier = f"{store_code}_{author}_{review_text}_{rating}"
        hash_val = hashlib.md5(identifier.encode("utf-8")).hexdigest()
        return hash_val
    except Exception as e:
        # DOM 속성으로 대체 식별 시도
        try:
            # 카드의 DOM 위치 정보 활용
            position = str(card_element.location)
            return hashlib.md5(f"pos_{position}".encode()).hexdigest()
        except:
            # 마지막 대안: 랜덤 ID (동일 세션에서만 사용)
            import random
            return f"random_{random.randint(10000, 99999)}"

def extract_review_data_from_network(driver, card_element):
    """
    네트워크 요청에서 리뷰 ID와 작성 날짜를 추출하는 함수
    """
    try:
        # 카드 요소가 화면에 보이도록 스크롤
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
            card_element
        )
        time.sleep(1)
        
        # 브라우저 콘솔에서 실행할 스크립트
        # 페이지에 있는 리뷰 데이터를 찾고 캡처하는 코드
        script = """
        // 기존 데이터 초기화
        window.capturedReviewData = [];
        
        // XHR 요청을 가로채는 함수
        let originalXHROpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function() {
            this.addEventListener('load', function() {
                try {
                    // reviews 관련 요청만 필터링
                    if (this.responseURL.includes('/reviews') || 
                        this.responseURL.includes('memberNickname')) {
                        let response = JSON.parse(this.responseText);
                        
                        // 리뷰 데이터 추출 로직 - 응답 구조에 따라 조정 필요
                        if (response.contents || response.data?.contents) {
                            let reviews = response.contents || response.data.contents;
                            if (Array.isArray(reviews)) {
                                window.capturedReviewData = window.capturedReviewData.concat(reviews);
                            }
                        }
                    }
                } catch (e) {
                    console.error('Review data capture error:', e);
                }
            });
            originalXHROpen.apply(this, arguments);
        };
        
        // 이미 페이지에 로드된 데이터 확인
        function findReviewsInGlobalVars() {
            // 자주 사용되는 전역 변수 이름들 확인
            const possibleVars = ['__INITIAL_STATE__', 'REVIEW_DATA', 'window.reviewData'];
            
            for (let varName of possibleVars) {
                try {
                    let data = eval(varName);
                    if (data && (data.reviewContents?.contents || data.contents)) {
                        window.capturedReviewData = window.capturedReviewData.concat(
                            data.reviewContents?.contents || data.contents
                        );
                    }
                } catch (e) {
                    // 변수가 없거나 접근할 수 없음, 계속 진행
                }
            }
            
            // DOM에서 직접 데이터 속성 찾기
            document.querySelectorAll('[data-review]').forEach(el => {
                try {
                    const data = JSON.parse(el.dataset.review);
                    window.capturedReviewData.push(data);
                } catch (e) {
                    // 파싱 오류, 계속 진행
                }
            });
        }
        
        // 페이지에 이미 있는 데이터 확인
        findReviewsInGlobalVars();
        
        // 디버깅 정보
        return {
            reviewCount: window.capturedReviewData.length,
            reviewSample: window.capturedReviewData.slice(0, 3), // 처음 3개 샘플만 반환
            allData: window.capturedReviewData // 필요시 모든 데이터 반환
        };
        """
        
        # 스크립트 실행
        result = driver.execute_script(script)
        
        # 로그 출력
        print(f"[extract_review_data_from_network] 캡처된 리뷰 수: {result.get('reviewCount', 0)}")
        
        # 리뷰 데이터가 없으면 None 반환
        if not result.get('reviewCount', 0):
            return None
            
        # 모든 캡처된 리뷰 데이터
        all_reviews = result.get('allData', [])
        
        # 작성자와 리뷰 텍스트로 현재 카드에 맞는 리뷰 찾기
        try:
            author_el = card_element.find_element(By.CSS_SELECTOR, 'p.nick')
            author = author_el.text.strip()
            
            # 리뷰 내용 (없을 수도 있음)
            try:
                review_text = card_element.find_element(By.CSS_SELECTOR, 'p.review-cont').text.strip()
            except:
                review_text = ""
                
            # 별점
            try:
                rating_els = card_element.find_elements(
                    By.CSS_SELECTOR, 'div.rating-stars > svg > path[fill="#FFC600"]'
                )
                rating = len(rating_els)
            except:
                rating = 0
                
            # 현재 카드에 해당하는 리뷰 찾기
            matching_review = None
            for review in all_reviews:
                # 작성자로 1차 필터링
                if review.get('memberNickname') == author:
                    # 리뷰 내용이 있으면 내용으로도 확인
                    if not review_text or review.get('contents') == review_text:
                        matching_review = review
                        break
            
            if matching_review:
                # ID와 작성일 추출
                review_id = str(matching_review.get('id', ''))
                created_at = matching_review.get('createdAt', '')
                
                # 날짜 포맷 변환
                try:
                    # ISO 형식 또는 타임스탬프 형식을 처리
                    if isinstance(created_at, str) and created_at:
                        from datetime import datetime
                        if 'T' in created_at:  # ISO 형식 (2023-05-25T17:18:31.607359)
                            created_at = datetime.fromisoformat(created_at.split('.')[0])
                        else:  # 타임스탬프 가능성
                            created_at = datetime.fromtimestamp(int(created_at) / 1000)
                        review_date = created_at.strftime('%Y-%m-%d')
                    else:
                        # 날짜가 숫자 형식이면 타임스탬프로 처리
                        from datetime import datetime
                        if isinstance(created_at, (int, float)):
                            review_date = datetime.fromtimestamp(created_at / 1000).strftime('%Y-%m-%d')
                        else:
                            # 날짜 정보 없으면 ID에서 추출 시도
                            review_date = extract_date_from_review_id(review_id)
                except Exception as e:
                    print(f"[extract_review_data_from_network] 날짜 변환 오류: {str(e)}")
                    review_date = extract_date_from_review_id(review_id)
                
                print(f"[extract_review_data_from_network] 리뷰 ID: {review_id}, 날짜: {review_date}")
                return {
                    'review_id': review_id,
                    'review_date': review_date,
                    'full_data': matching_review  # 추가 정보가 필요할 경우
                }
            else:
                print(f"[extract_review_data_from_network] 일치하는 리뷰를 찾을 수 없음: {author}, {review_text[:20]}")
        
        except Exception as e:
            print(f"[extract_review_data_from_network] 리뷰 매칭 중 오류: {str(e)}")
            
        return None
        
    except Exception as e:
        print(f"[extract_review_data_from_network] 네트워크 데이터 추출 오류: {str(e)}")
        return None

###################################################
# 2) 설정 파일 관련 함수
###################################################
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4)

###################################################
# 3) 데이터 가져오기 관련 함수
###################################################
def fetch_platform_data():
    try:
        response = supabase.table("platform_reply_rules") \
                           .select("*") \
                           .in_("platform", ["배민", "배민1"]) \
                           .execute()
        rows = response.data
        if not rows:
            print("플랫폼 데이터가 비어있음.")
            return []

        data_list = []
        for r in rows:
            item = {
                "store_code": r.get("store_code", ""),
                "platform": r.get("platform", ""),
                "platform_code": r.get("platform_code", ""),
                "platform_id": r.get("platform_id", ""),
                "platform_pw": r.get("platform_pw", ""),
                "greeting_start": r.get("greeting_start", ""),
                "greeting_end": r.get("greeting_end"),
                "role": r.get("role", ""),
                "tone": r.get("tone", ""),
                "max_length": r.get("max_length", 300),
                "rating_5_reply": r.get("rating_5_reply", True),
                "rating_4_reply": r.get("rating_4_reply", True),
                "rating_3_reply": r.get("rating_3_reply", True),
                "rating_2_reply": r.get("rating_2_reply", True),
                "rating_1_reply": r.get("rating_1_reply", True),
                "store_name": r.get("store_name", ""),
                "store_type": r.get("store_type")
            }
            data_list.append(item)
        print(f"[fetch_platform_data] 플랫폼 데이터 개수: {len(data_list)}")
        return data_list
    except Exception as e:
        print(f"[fetch_platform_data] 플랫폼 데이터 가져오기 실패: {str(e)}")
        return []

def group_by_credentials(data_list):
    grouped = {}
    for item in data_list:
        key = (item["platform_id"], item["platform_pw"])
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(item)
    print(f"[group_by_credentials] 그룹화된 계정 수: {len(grouped)}")
    return grouped

###################################################
# 4) 에러 처리 관련 함수
###################################################
def save_error_log_to_supabase(
    category: str,
    platform: str,
    store_code: str,
    error_type: str,
    error_message: str,
    stack_trace: str = ""
):
    try:
        # platform_reply_rules에서 store_name 조회
        store_query = supabase.table("platform_reply_rules") \
                             .select("store_name") \
                             .eq("store_code", store_code) \
                             .eq("platform", platform) \
                             .execute()
        store_name = store_query.data[0].get("store_name", "") if store_query.data else ""

        data = {
            "store_code": store_code,
            "category": category,
            "platform": platform,
            "error_type": error_type,
            "error_message": error_message,
            "stack_trace": stack_trace,
            "occurred_at": datetime.now().isoformat(),
            "store_name": store_name
        }
        supabase.table("error_logs").insert(data).execute()
        print(f"[save_error_log_to_supabase] 에러 로그 저장 완료 - store_code={store_code}, error_type={error_type}, msg={error_message}")
    except Exception as e:
        print(f"[save_error_log_to_supabase] 에러 로그 저장 실패: {str(e)}")

def save_error_screenshot(driver, store_code, error_type):
    try:
        screenshot_dir = "배민_[오류]스크린샷"
        if not os.path.exists(screenshot_dir):
            os.makedirs(screenshot_dir)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{store_code}_{error_type}_{timestamp}.png"
        filepath = os.path.join(screenshot_dir, filename)

        driver.save_screenshot(filepath)
        print(f"[save_error_screenshot] 스크린샷 저장: {filepath}")
        return filepath
    except Exception as e:
        print(f"[save_error_screenshot] 스크린샷 저장 실패: {str(e)}")
        return None

###################################################
# 5) 팝업 처리 관련 함수
###################################################
def close_popups_on_homepage(driver, timeout=5):
    try:
        popup1_close = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.ID, 'btn-close-nday'))
        )
        popup1_close.click()
        print("[close_popups_on_homepage] 홈페이지 팝업1 닫음")
    except TimeoutException:
        pass
    except Exception as e:
        print(f"[close_popups_on_homepage] 팝업 닫기 에러1: {e}")

    try:
        popup2_close = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(@aria-label, '닫기')]"))
        )
        popup2_close.click()
        print("[close_popups_on_homepage] 홈페이지 팝업2 닫음")
    except TimeoutException:
        pass
    except Exception as e:
        print(f"[close_popups_on_homepage] 팝업 닫기 에러2: {e}")

def close_popups_on_review_page(driver, timeout=5):
    try:
        popup_close = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.button-overlay.css-fowwyy'))
        )
        driver.execute_script("arguments[0].click();", popup_close)
        print("[close_popups_on_review_page] 리뷰페이지 팝업 닫음")
    except TimeoutException:
        pass
    except Exception as e:
        print(f"[close_popups_on_review_page] 리뷰 페이지 팝업 닫기 실패: {e}")

def close_today_popup(driver, timeout=5):
    """
    '오늘 하루 보지 않기' 버튼이 있는 팝업을 처리하는 함수
    """
    try:
        today_btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((
                By.XPATH, 
                "//span[contains(@class, 'TextButton') and .//span[contains(text(), '오늘 하루 보지 않기')]]"
            ))
        )
        driver.execute_script("arguments[0].click();", today_btn)
        print("[close_today_popup] '오늘 하루 보지 않기' 팝업 닫음")
        time.sleep(1)
    except TimeoutException:
        print("[close_today_popup] '오늘 하루 보지 않기' 버튼 없음")
    except Exception as e:
        print(f"[close_today_popup] 팝업 닫기 중 오류: {str(e)}")
        
def close_7day_popup(driver, timeout=5):
    """
    7일간 보지 않기 → 1일간 보지 않기 → 오늘 하루 보지 않기 팝업을 처리하는 함수
    """
    try:
        btn_7day = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., '7일간 보지 않기')]"))
        )
        driver.execute_script("arguments[0].click();", btn_7day)
        print("[close_7day_popup] 7일간 보지 않기 클릭")
        time.sleep(3)
        
        try:
            btn_1day = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(., '1일간 보지 않기')]"))
            )
            driver.execute_script("arguments[0].click();", btn_1day)
            print("[close_7day_popup] 1일간 보지 않기 클릭")
        except TimeoutException:
            print("[close_7day_popup] 1일간 보지 않기 버튼 없음")
        except Exception as e:
            print(f"[close_7day_popup] 1일간 보지 않기 처리 중 오류: {str(e)}")

        try:
            today_btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(), '오늘 하루 보지 않기')]]"))
            )
            driver.execute_script("arguments[0].click();", today_btn)
            print("[close_7day_popup] 오늘 하루 보지 않기 클릭")
        except TimeoutException:
            print("[close_7day_popup] 오늘 하루 보지 않기 버튼 없음")
        except Exception as e:
            print(f"[close_7day_popup] 오늘 하루 보지 않기 처리 중 오류: {str(e)}")

    except TimeoutException:
        print("[close_7day_popup] 7일간 보지 않기 버튼 없음")
    except Exception as e:
        print(f"[close_7day_popup] 7일간 보지 않기 처리 중 오류: {str(e)}")

    # 추가 7일/1주일 팝업 처리
    try:
        week_popup = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((
                By.XPATH, 
                "//span[contains(@class, 'TextButton') and contains(., '일주일 동안 보지 않기')]"
            ))
        )
        driver.execute_script("arguments[0].click();", week_popup)
        print("[close_7day_popup] 일주일 보지 않기 팝업 닫음")
    except TimeoutException:
        print("[close_7day_popup] 일주일 보지 않기 팝업 없음")
    except Exception as e:
        print(f"[close_7day_popup] 일주일 보지 않기 팝업 처리 중 오류: {str(e)}")

###################################################
# 6) 안티봇 및 창 처리 관련 함수
###################################################
def check_antibot(driver):
    try:
        antibot_texts = driver.find_elements(
            By.XPATH,
            "//*[contains(text(),'로봇이')] | "
            "//*[contains(text(),'자동화된')] | "
            "//*[contains(text(),'확인하십시오')]"
        )
        return len(antibot_texts) > 0
    except Exception as e:
        print(f"[check_antibot] 안티봇 체크 중 에러: {str(e)}")
        return False

def restart_driver(driver, options=None):
    try:
        if driver:
            driver.quit()
        time.sleep(5)
        if not options:
            options = uc.ChromeOptions()
            options.add_argument("--start-maximized")
        new_driver = uc.Chrome(options=options)
        print("[restart_driver] 드라이버 재시작 완료")
        return new_driver
    except Exception as e:
        print(f"[restart_driver] 드라이버 재시작 실패: {str(e)}")
        return None

def handle_new_windows(driver, store_code=None, main_window=None):
    """
    새로 열린 창을 감지하고 닫는 함수
    """
    try:
        if not main_window:
            main_window = driver.current_window_handle
        handles = driver.window_handles
        for handle in handles:
            if handle != main_window:
                try:
                    driver.switch_to.window(handle)
                    store_info = f"[{store_code}] " if store_code else ""
                    print(f"{store_info}[새창감지] title='{driver.title}' => 닫기 시도")
                    driver.close()
                    time.sleep(1)
                except Exception as ex:
                    print(f"{store_info}[새창닫기실패] handle={handle}, error={str(ex)}")
        driver.switch_to.window(main_window)
        return True
    except Exception as ex:
        store_info = f"[{store_code}] " if store_code else ""
        print(f"{store_info}[새창처리오류] {str(ex)}")
        try:
            driver.switch_to.window(main_window)
        except:
            pass
        return False

def check_and_close_new_windows(driver, store_code=None):
    main_window = driver.current_window_handle
    curr_count = len(driver.window_handles)
    if curr_count > 1:
        store_info = f"[{store_code}] " if store_code else ""
        print(f"{store_info}[창감지] {curr_count}개 창 발견")
        handle_new_windows(driver, store_code, main_window)
    return main_window

def check_windows_periodically(driver, store_code=None, interval_seconds=30):
    last_check = time.time()
    curr_time = time.time()
    if curr_time - last_check >= interval_seconds:
        check_and_close_new_windows(driver, store_code)
        last_check = curr_time

###################################################
# 7) 로그인 처리 함수
###################################################
def login_to_baemin(driver, platform_id, platform_pw, store_code, platform_name, options=None):
    max_attempts = 3
    for attempt in range(1, max_attempts+1):
        try:
            print(f"[login_to_baemin] 로그인 시도 {attempt}/{max_attempts}, ID={platform_id}, store_code={store_code}")
            driver.get("https://biz-member.baemin.com/login?returnUrl=https%3A%2F%2Fceo.baemin.com%2F")
            time.sleep(3)

            if check_antibot(driver):
                print("[login_to_baemin] 안티봇 감지 -> 드라이버 재시작")
                driver = restart_driver(driver, options)
                if not driver:
                    return False, None
                continue

            wait = WebDriverWait(driver, 5)
            id_input = wait.until(EC.presence_of_element_located((By.NAME, 'id')))
            pw_input = wait.until(EC.presence_of_element_located((By.NAME, 'password')))
            id_input.clear()
            id_input.send_keys(platform_id)
            pw_input.clear()
            pw_input.send_keys(platform_pw)
            pw_input.send_keys(Keys.RETURN)
            time.sleep(5)

            if check_antibot(driver):
                print("[login_to_baemin] 로그인 후 안티봇 감지 -> 재시작")
                driver = restart_driver(driver, options)
                if not driver:
                    return False, None
                continue

            error_msgs = driver.find_elements(By.XPATH, "//p[@role='alert']")
            if error_msgs:
                for msg_el in error_msgs:
                    errtxt = msg_el.text.strip()
                    if errtxt:
                        print(f"[login_to_baemin] 로그인 에러 메시지 감지: {errtxt}")
                if attempt == max_attempts:
                    msg = f"[login_to_baemin] {store_code} 로그인 실패(최대 시도)"
                    print(msg)
                    save_error_screenshot(driver, store_code, "LoginFail")
                    save_error_log_to_supabase(
                        category="오류",
                        platform=platform_name,
                        store_code=store_code,
                        error_type="로그인 실패",
                        error_message=msg
                    )
                    return False, driver
                continue

            close_popups_on_homepage(driver)
            print(f"[login_to_baemin] {store_code} 로그인 성공")
            return True, driver

        except Exception as e:
            if attempt == max_attempts:
                msg = f"[login_to_baemin] {store_code} 로그인 중 오류: {str(e)}"
                print(msg)
                save_error_screenshot(driver, store_code, "LoginError")
                save_error_log_to_supabase(
                    category="오류",
                    platform=platform_name,
                    store_code=store_code,
                    error_type="로그인 실패",
                    error_message=str(e)
                )
                return False, driver
            time.sleep(2)
    return False, driver

###################################################
# 8) AI 리뷰 분석 관련 함수
###################################################
def analyze_restaurant_review(review_text: str, rating: int, order_menu: str = "", delivery_review: str = "") -> dict:
    """음식점 리뷰를 더 세부적으로 분석하여 AI 답변 가능 여부와 상세 정보를 반환"""
    
    try:
        # 1. 기본 정보 로깅
        print("\n" + "="*50)
        print("[분석 시작] 리뷰 정보")
        print(f"별점: {rating}점")
        print(f"리뷰: {review_text}")
        print(f"주문메뉴: {order_menu}")
        print(f"배달리뷰: {delivery_review}")
        print("="*50)

        # 2. 입력값 검증
        if not openai.api_key:
            raise ValueError("OpenAI API 키가 설정되지 않음")

        # 3. 세부 분석을 위한 프롬프트 강화
        system_prompt = """
음식점 리뷰 분석 전문가로서 다음 기준으로 분석해주세요:

1. 감성 분석
- 긍정/부정 키워드 식별
- 구체적인 불만 사항 체크
- 감정 강도 측정 (0~1)

2. 리뷰 카테고리 분류
[음식 품질]
- 맛, 양, 신선도, 온도
- 포장 상태
- 위생 상태

[서비스 품질]
- 배달 시간
- 주문 정확도
- 응대 태도

[가격/정책]
- 가격 관련 피드백
- 메뉴 구성 관련
- 영업 방침 관련

3. 심각도 판단
- 위생 문제 여부
- 이물질 발견 여부
- 알레르기/건강 이슈
- 반복적 문제 제기

4. AI 답변 가능성 평가
- 단순 피드백 vs 심각한 불만
- 구체적 조치 필요성
- 법적 이슈 가능성

분석 결과는 다음 JSON 형식으로만 반환:
{
    "ai_reply": true/false,
    "sentiment_score": 0.0~1.0,
    "category": "메인_카테고리",
    "sub_category": "세부_카테고리",
    "keywords": ["주요", "키워드", "목록"],
    "severity": "LOW/MEDIUM/HIGH",
    "reason": "판단근거",
    "action_needed": ["필요한", "조치", "사항"]
}"""

        user_prompt = f"""
별점: {rating}점
주문메뉴: {order_menu}
배달리뷰: {delivery_review}
리뷰내용: {review_text if review_text else '(리뷰 내용 없음)'}
"""

        # 4. 별점만 있는 경우 빠른 처리
        if not review_text or review_text.strip() == "":
            return _handle_rating_only_review(rating)

        # 5. GPT 분석 요청
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=300
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # 6. 결과 보정 및 검증
            result = _adjust_analysis_result(result, rating)
            result = _validate_analysis_result(result)
            if result.get("sentiment_score", 0) >= 0.5:
                result["ai_reply"] = True

            # 7. 상세 로깅
            print("\n[분석 결과]")
            for key, value in result.items():
                print(f"- {key}: {value}")
            print("="*50 + "\n")
            
            return result

        except json.JSONDecodeError as e:
            print(f"[분석 실패] JSON 파싱 오류: {str(e)}")
            return _create_error_result("PARSE_ERROR", str(e))
            
        except Exception as e:
            print(f"[분석 실패] GPT 요청 오류: {str(e)}")
            return _create_error_result("GPT_ERROR", str(e))

    except Exception as e:
        print(f"[분석 실패] 예상치 못한 오류: {str(e)}")
        return _create_error_result("UNKNOWN_ERROR", str(e))

def _handle_rating_only_review(rating: int) -> dict:
    """별점만 있는 리뷰 처리"""
    if rating >= 4:
        return {
            'ai_reply': True,
            'sentiment_score': 0.8,
            'category': 'RATING_ONLY',
            'sub_category': 'HIGH_RATING',
            'keywords': ['별점', '만족'],
            'severity': 'LOW',
            'reason': f'텍스트 없는 {rating}점 긍정 리뷰',
            'action_needed': ['기본 감사 인사']
        }
    elif rating <= 2:
        return {
            'ai_reply': False,
            'sentiment_score': 0.2,
            'category': 'RATING_ONLY',
            'sub_category': 'LOW_RATING',
            'keywords': ['별점', '불만'],
            'severity': 'HIGH',
            'reason': f'텍스트 없는 {rating}점 부정 리뷰',
            'action_needed': ['사장님 직접 확인']
        }
    else:
        return {
            'ai_reply': True,
            'sentiment_score': 0.5,
            'category': 'RATING_ONLY',
            'sub_category': 'MID_RATING',
            'keywords': ['별점', '중립'],
            'severity': 'MEDIUM',
            'reason': f'텍스트 없는 {rating}점 중립 리뷰',
            'action_needed': ['일반 응대']
        }

def _adjust_analysis_result(result: dict, rating: int) -> dict:
    """분석 결과 보정"""
    # 1. 감성 점수 보정
    if rating >= 4 and result['sentiment_score'] < 0.3:
        result['sentiment_score'] = max(0.4, result['sentiment_score'])
    elif rating <= 2 and result['sentiment_score'] > 0.7:
        result['sentiment_score'] = min(0.6, result['sentiment_score'])
    
    # 2. 심각도 기반 AI 답변 여부 결정
    if result['severity'] == 'HIGH':
        result['ai_reply'] = False
    
    return result

def _validate_analysis_result(result: dict) -> dict:
    """분석 결과 검증"""
    required_fields = {
        'ai_reply': bool,
        'sentiment_score': float,
        'category': str,
        'sub_category': str,
        'keywords': list,
        'severity': str,
        'reason': str,
        'action_needed': list
    }
    
    for field, field_type in required_fields.items():
        if field not in result:
            result[field] = field_type()
        elif not isinstance(result[field], field_type):
            result[field] = field_type(result[field])
    
    return result

def _create_error_result(error_type: str, error_msg: str) -> dict:
    """에러 결과 생성"""
    return {
        'ai_reply': False,
        'sentiment_score': 0.5,
        'category': error_type,
        'sub_category': 'ERROR',
        'keywords': ['에러'],
        'severity': 'HIGH',
        'reason': error_msg,
        'action_needed': ['사장님 확인']
    }

###################################################
# 9) AI 답변 생성 관련 함수
###################################################
def clean_ai_reply(ai_reply):
    """AI 답변 텍스트 정리"""
    ai_reply = unicodedata.normalize('NFC', ai_reply)
    filtered = []
    for c in ai_reply:
        # 백슬래시를 포함한 문자열 등 문법 문제 방지
        if ord(c) <= 0xFFFF and (c.isprintable() or c in ('\n', '\r', '\t')):
            filtered.append(c)
    return "".join(filtered).strip()

def validate_reply_content(ai_reply: str) -> tuple[bool, str]:
    """
    AI 답변의 내용을 검증하고 문제가 있는 경우 거부합니다.
    
    Returns:
        (is_valid: bool, reason: str)
    """
    # 한글 외 문자 비율 검사
    total_chars = len(ai_reply)
    if total_chars == 0:
        return False, "빈 답변"
        
    # 한글, 기본 문장 부호, 숫자만 허용
    valid_pattern = re.compile(r'[가-힣\s.,!?0-9%]')
    valid_chars = len([c for c in ai_reply if valid_pattern.match(c)])
    
    # 허용되지 않는 문자 비율이 10% 이상이면 거부
    invalid_ratio = (total_chars - valid_chars) / total_chars
    if invalid_ratio > 0.1:
        return False, "허용되지 않는 문자 과다 사용"
    
    # 기본 문장 구조 검사
    if '\n' not in ai_reply:
        return False, "줄바꿈 누락 오류"

    # 특수 패턴 검사
    suspicious_patterns = [
        (r'[ぁ-んァ-ン]', "일본어 사용"),
        (r'[一-龯]', "한자 사용"),
        (r'[?!.]{2,}', "과도한 문장부호 사용"),
        (r'[ㅋㅎㄷ]{3,}', "의성어/의태어 과다 사용")
    ]
    
    for pattern, reason in suspicious_patterns:
        if re.search(pattern, ai_reply):
            return False, reason
            
    return True, ""

def generate_ai_reply(review_text, store_info):
    """AI 답변 생성"""
    try:
        if not openai.api_key:
            print("[generate_ai_reply] OpenAI API 키가 설정되지 않음.")
            return None

        print(f"[generate_ai_reply] 리뷰 텍스트: {review_text[:30]}...")
        is_empty_review = not review_text or review_text.strip() == ""

        store_type = store_info.get('store_type', 'delivery_only')
        greeting_start = store_info.get('greeting_start', '안녕하세요')
        
        # greeting_end 처리 개선
        greeting_end = store_info.get('greeting_end')
        if greeting_end is None or greeting_end.strip() == '':
            # 종료 인사 지시만 전달하고 구체적인 문구는 AI가 선택하도록 함
            greeting_end_prompt = "상황에 맞는 다양한 종료 인사 사용"
        else:
            greeting_end_prompt = f"정확히 '{greeting_end}'를 종료 인사로 사용"
            
        max_length = store_info.get('max_length')  # 기본값 추가
        rating = store_info.get('rating', 0)
        author = store_info.get('author', '고객')
        
        # 금지어 회피 지시 추가
        avoid_words_prompt = ""
        if store_info.get('avoid_words'):
            avoid_words = store_info.get('avoid_words')
            if isinstance(avoid_words, list):
                avoid_words_prompt = f"\n\n## 중요: 금지어\n다음 단어를 절대 사용하지 마세요: {', '.join(avoid_words)}"
            else:
                avoid_words_prompt = f"\n\n## 중요: 금지어\n{avoid_words}"

        # Split the system prompt into parts to avoid f-string backslash issues
        system_prompt_parts = [
            f"당신은 {store_type} 유형의 가게 사장님입니다.",
            "",
            "## 기본 응대 원칙",
            "1. 고객의 구체적인 피드백 포인트를 반드시 언급하며 답변",
            "2. 부정적 리뷰에도 변명하지 않고 개선 의지를 진정성 있게 전달",
            "3. 긍정적 피드백은 구체적으로 감사를 표현하고 더 나은 서비스 약속",
            "4. 고객의 의견을 경청하고 존중하는 태도 유지",
            "",
            "## 매장 유형별 커뮤니케이션"
        ]

        # Add store type specific messaging
        if store_type == 'delivery_only':
            system_prompt_parts.append("- 배달 전용: 신속/정확한 배달, 포장 품질, 음식 온도 유지에 중점을 둔 답변")
        else:
            system_prompt_parts.append("- 홀+배달: 홀에 대한 질문이 있는 경우에만 홀 운영에 대해 답변")

        # Continue with the rest of the prompt
        system_prompt_parts.extend([
            "",
            "## 답변 형식",
            f"- 길이: {max_length}자 내외",
            "- 문체: 정중하고 친근한 한국어 (반말 사용 금지)",
            "- 호칭: 이름 뒤 '님'을 붙여쓰기 (띄어쓰기 없이 '닉네임님'으로 작성)",
            f"- 시작: '{greeting_start}'",
            f"- 종료: {greeting_end_prompt}",
            "- 줄바꿈 형식: 각 문단 사이에 빈 줄 없이 줄바꿈만 사용",
            "",
            "## 종료 인사 가이드",
            "- greeting_end 값이 제공된 경우, 제공된 정확한 문구를 종료 인사로 사용하세요",
            "- greeting_end 값이 제공되지 않은 경우, 리뷰 내용과 상황에 적합하게 작성하세요:",
            "- 리뷰 내용과 맥락에 맞는 자연스러운 종료 인사를 선택하되, 정중함을 유지하세요",
            "",
            "## 상황별 대응 가이드",
            "- 5점 + 리뷰 없음: 높은 평가 감사 + 구체적인 서비스 약속",
            "- 칭찬 리뷰: 구체적 감사 + 해당 장점 더욱 강화 약속",
            "- 개선 요청: 겸손한 수용 + 구체적 개선 계획 제시",
            "- 배달 관련: 시간/포장/온도 등 구체적 개선 방안 언급",
            "",
            "## 금지 사항",
            "- 변명조의 답변",
            "- 형식적인 사과",
            "- 추상적인 답변",
            "- 과도한 미사여구",
            "- 부정적 단어 사용"
        ])
        
        # 금지어 회피 지시 추가
        if avoid_words_prompt:
            system_prompt_parts.append(avoid_words_prompt)

        # Join all parts with newlines
        system_prompt = "\n".join(system_prompt_parts)

        user_prompt = f"""리뷰 작성자 이름과 별점, 리뷰 내용을 보고 답변을 작성해주세요:
        작성자: {author}
        별점: {rating}점
        리뷰: {review_text if not is_empty_review else '(리뷰 내용 없음)'}

        주의사항: 
        1. 작성자 호칭은 '{author}님' 으로 붙여서 사용하세요 (띄어쓰기 없이)
        2. 줄바꿈은 자연스럽게 사용하되, 문단을 구분하는 빈 줄은 넣지 마세요
        (예: 인사말 후 줄바꿈은 가능하지만, 빈 줄을 포함한 문단 구분은 피하세요)
        3. 다음과 같은 형식으로 답변해주세요: (빈 줄 없이 각 줄이 바로 이어지도록록)
        안녕하세요! {author}님
        리뷰 감사합니다. 내용...
        또 다른 내용...
        감사합니다."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.7,
            max_tokens=600
        )
        ai_reply = response.choices[0].message.content
        ai_reply = clean_ai_reply(ai_reply)
        print(f"[generate_ai_reply] 생성된 답글: {ai_reply[:40]}...")
        return ai_reply

    except Exception as e:
        print(f"[generate_ai_reply] AI 답변 생성 오류: {e}")
        return None
    
def generate_ai_reply_with_retry(review_text: str, store_info: dict, max_attempts: int = 3) -> str:
    """품질 검증을 통과할 때까지 최대 3회까지 재시도하는 답변 생성 함수"""
    
    best_reply = None
    best_score = 0
    
    for attempt in range(max_attempts):
        # AI 답변 생성
        ai_reply = generate_ai_reply(review_text, store_info)
        if not ai_reply:
            continue
            
        # 1차: 형식/내용 검증
        is_valid, reason = validate_reply_content(ai_reply)
        if not is_valid:
            print(f"[Attempt {attempt + 1}] 검증 실패: {reason}")
            continue
        
        # 2차: 품질 점수 평가
        is_good, scores = score_reply(ai_reply, review_text, 
                          store_info.get('author', ''), 
                          store_info.get('rating', 0))
        
        # 문자열 비교 위해 점수 변환
        score = sum(scores.values()) if isinstance(scores, dict) else 70
        print(f"[Attempt {attempt + 1}] 점수: {score}")
        
        # 더 나은 답변 저장
        if score > best_score:
            best_score = score
            best_reply = ai_reply
            
        # 목표 점수 달성시 즉시 반환
        if is_good:
            return ai_reply
            
    # 모든 시도 후에도 80점을 넘지 못하면 최고 점수 답변 반환
    if best_reply:
        print(f"[Warning] 최고 점수({best_score})로 답변 생성")
        return best_reply
        
    raise Exception("유효한 답변 생성 실패")

###################################################
# 10) AI 답변 품질 평가 함수
###################################################
def score_reply(ai_reply: str, original_review: str, original_author: str = "", original_rating: int = 0, threshold: int = 80) -> tuple[bool, dict]:
    """
    AI 답변의 품질을 세부적으로 평가합니다.
    Returns:
        tuple[총점, 세부점수_딕셔너리]
    """
    try:
        if not openai.api_key:
            print("[score_reply] OpenAI API 키 없음")
            return True, {"총점": 80}  # API 키가 없으면 기본적으로 통과

        system_prompt = """
리뷰 답글의 품질을 다음 기준으로 평가하여 구체적인 점수를 매기세요:

1. 맥락 이해도 (30점)
- 리뷰 내용 정확한 파악: 10점
- 주문 메뉴 언급: 10점
- 고객 감정 대응: 10점

2. 전문성 (20점)
- 메뉴/서비스 관련 전문성: 10점
- 개선사항 구체성: 10점

3. 형식 완성도 (20점)
- 인사말/맺음말: 5점
- 답변 구조: 5점
- 적절한 길이: 5점
- 단락 구분: 5점

4. 어조와 태도 (15점)
- 공손함: 5점
- 진정성: 5점
- 고객 존중: 5점

5. 문장 품질 (15점)
- 맞춤법: 5점
- 문장 자연스러움: 5점
- 용어 적절성: 5점

점수 형식 예시:
{
    "맥락 이해도": 25,
    "전문성": 18,
    "형식 완성도": 18,
    "어조와 태도": 14,
    "문장 품질": 13,
    "총점": 88
}

반드시 위 형식의 JSON 형태로 응답해주세요."""

        user_prompt = f"""
원본 리뷰 정보:
- 작성자: {original_author}
- 별점: {original_rating}점
- 내용: {original_review}

AI 답글:
{ai_reply}

각 평가 항목의 점수와 총점을 계산해주세요."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=300
        )

        try:
            content = response.choices[0].message.content
            # JSON 형식만 추출
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group(0))
            else:
                # JSON을 찾지 못하면 기본값 사용
                print(f"[score_reply] JSON 형식을 찾을 수 없음, 원본 응답: {content[:100]}...")
                return True, {"총점": 85}
                
            # 점수 계산 및 검증
            total_score = result.get("총점", 0)
            if total_score == 0:
                # 총점이 없으면 항목 점수 합산
                total_score = sum([v for k, v in result.items() if k != "총점" and isinstance(v, (int, float))])
                result["총점"] = total_score
                
            # 세부 점수 로깅
            print("\n[답변 품질 평가]")
            print(f"총점: {total_score}")
            for category, score in result.items():
                if category != "총점":
                    print(f"- {category}: {score}점")
            
            return total_score >= threshold, result
            
        except json.JSONDecodeError as e:
            print(f"[score_reply] JSON 파싱 오류: {e}, 원본 응답: {content[:100]}...")
            # 오류 발생시 기본적으로 통과 처리
            return True, {"총점": 80}

    except Exception as e:
        print(f"[score_reply] 평가 중 오류: {str(e)}")
        # 오류 발생시 기본적으로 통과 처리
        return True, {"총점": 80}

###################################################
# 11) 리뷰 저장 및 관리 함수
###################################################
def _save_review_data(
    store_code, platform, platform_code, review_name, rating, 
    review_content, ai_response, review_date, status, store_name, 
    review_id, ordered_menu="", delivery_review="", # 추가된 매개변수
    retry_count=0, boss_reply_needed=False, 
    review_category="", review_reason=""
):
    """리뷰 데이터 저장/업데이트 함수"""
    try:
        data = {
            "store_code": store_code,
            "platform": platform,
            "platform_code": platform_code,
            "review_name": review_name,
            "rating": rating,
            "ordered_menu": ordered_menu,      # 추가
            "delivery_review": delivery_review,  # 추가
            "review_content": review_content,
            "ai_response": ai_response,
            "store_name": store_name,
            "response_status": status,
            "response_at": datetime.now().isoformat() if status == "답변완료" else None,
            "review_date": review_date,
            "review_id": review_id,
            "boss_reply_needed": boss_reply_needed,
            "review_category": review_category,
            "review_reason": review_reason,
            "retry_count": retry_count,
            "updated_at": datetime.now().isoformat()
        }
        
        # 기존 레코드 확인
        existing = supabase.table("reviews").select("id").eq("review_id", review_id).execute()
        
        if existing.data:
            # 업데이트
            record_id = existing.data[0]['id']
            supabase.table("reviews").update(data).eq("id", record_id).execute()
            print(f"[_save_review_data] 리뷰 업데이트: id={record_id}, status={status}, retry={retry_count}")
        else:
            # 신규 저장
            supabase.table("reviews").insert(data).execute()
            print(f"[_save_review_data] 새 리뷰 저장: status={status}, retry={retry_count}")
            
    except Exception as e:
        print(f"[_save_review_data] 저장 실패: {str(e)}")

def _check_duplicate_review(driver, store_code, platform_name, review_hash, author, review_text):
    """
    중복 리뷰 체크 개선 버전:
    1. 리뷰가 DB에 존재하는지 확인
    2. 상태별 다른 처리:
       - "답변완료": 다시 처리하지 않음
       - "사장님 확인필요": 2일 이상 경과한 경우만 재처리
       - 그 외: 항상 재처리
    
    Returns:
        bool: True면 스킵, False면 계속 처리
    """
    try:
        today_date = datetime.now().date()
        
        # DB에서 기존 리뷰 조회
        existing = supabase.table("reviews").select("*").eq("review_id", review_hash).execute()
        
        if not existing.data:
            # 기존 리뷰 없음 - 계속 처리
            return False
            
        record = existing.data[0]
        status = record.get('response_status', '')
        record_date = datetime.fromisoformat(record.get('review_date', today_date.isoformat())).date()
        retry_count = record.get('retry_count', 0)
        
        # 재시도 횟수 초과 체크
        if retry_count >= 9:
            print(f"[_check_duplicate_review] 최대 재시도 횟수(5회) 초과: {review_hash[:8]}")
            return True  # 스킵
        
        if status == "답변완료":
            # 이미 답변 완료된 리뷰는 스킵
            print(f"[_check_duplicate_review] 이미 답변완료된 리뷰: {review_hash[:8]}")
            return True
            
        elif status == "사장님 확인필요":
            days_passed = (today_date - record_date).days
            
            if days_passed < 2:
                print(f"[_check_duplicate_review] 사장님 확인필요 리뷰 스킵 (경과일: {days_passed}일)")
                return True  # 2일 미만은 스킵
            else:
                print(f"[_check_duplicate_review] 사장님 확인필요 리뷰 재처리 (경과일: {days_passed}일)")
                return False  # 2일 이상은 재처리
        
        # 실패, 미답변 등 다른 상태는 재처리
        print(f"[_check_duplicate_review] 미완료 리뷰({status}) 재처리: {review_hash[:8]}")
        return False
            
    except Exception as e:
        # 조회 오류 시 안전하게 처리 진행
        print(f"[_check_duplicate_review] 중복 체크 중 오류: {str(e)}")
        return False

###################################################
# 12) 댓글 등록 처리 함수
###################################################
def click_and_submit_comment(driver, card_element, ai_reply_text, review_text, store_info, max_attempts=3):
    """개선된 댓글 입력 및 제출 처리 함수"""
    detected_prohibited_words = set()
    is_author_prohibited = False
    
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"[click_and_submit_comment] 댓글 등록 시도 {attempt}/{max_attempts}")
            
            # 1. 카드 영역이 보이도록 스크롤
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                card_element
            )
            time.sleep(1.5)  # 스크롤 후 충분히 대기

            # 2. 댓글 버튼 찾고 클릭 - 여러 방법 시도
            comment_btn = None
            
            # 2.1 직접적인 사장님 댓글 버튼 찾기 (새로운 선택자 추가)
            comment_selectors = [
                # 텍스트 기반 검색
                ".//button[contains(., '사장님 댓글')]",
                ".//button[contains(., '댓글')]",
                # 명확한 역할/ID가 있는 버튼
                ".//button[@role='button' and .//p[contains(text(), '사장님') or contains(text(), '댓글')]]",
                # 제공된 HTML 구조 기반
                ".//button[.//span[.//p[contains(text(), '사장님') or contains(text(), '댓글')]]]",
                # 모든 버튼 확인
                ".//button"
            ]
            
            for selector in comment_selectors:
                try:
                    buttons = card_element.find_elements(By.XPATH, selector)
                    for btn in buttons:
                        # 버튼 텍스트 확인
                        btn_text = btn.text.strip().lower()
                        if '사장님' in btn_text or '댓글' in btn_text or '답글' in btn_text:
                            comment_btn = btn
                            print(f"[click_and_submit_comment] 댓글 버튼 발견: '{btn_text}'")
                            break
                    if comment_btn:
                        break
                except Exception as e:
                    continue
            
            if not comment_btn:
                raise Exception("댓글 버튼을 찾을 수 없음")
            
            # 2.2 발견한 버튼 클릭 - 여러 방법 시도
            try:
                # JavaScript로 클릭
                driver.execute_script("arguments[0].click();", comment_btn)
                print("[click_and_submit_comment] 댓글 버튼 JavaScript 클릭")
            except Exception as js_click_error:
                try:
                    # Action Chains로 클릭
                    from selenium.webdriver.common.action_chains import ActionChains
                    action = ActionChains(driver)
                    action.move_to_element(comment_btn).pause(0.5).click().perform()
                    print("[click_and_submit_comment] 댓글 버튼 ActionChains 클릭")
                except Exception as action_click_error:
                    try:
                        # 일반 클릭
                        comment_btn.click()
                        print("[click_and_submit_comment] 댓글 버튼 일반 클릭")
                    except Exception as e:
                        raise Exception(f"댓글 버튼 클릭 실패: {str(e)}")
            
            # 3. 클릭 후 충분히 대기 (댓글 입력 영역이 나타날 때까지)
            time.sleep(2)
            
            # 4. 텍스트 영역 찾기 - 새로운 선택자 사용
            text_area = None
            textarea_selectors = [
                # 제공된 HTML 기반 정확한 선택자
                "textarea.TextArea_b_qmgb_12i8sxie",
                "textarea.TextArea_b_qmgb_12i8sxig",
                # 일반적인 textarea 태그
                "textarea",
                # XPATH 선택자
                "//textarea[@placeholder]",
                "//div[contains(@class, 'TextArea')]//textarea"
            ]
            
            for selector in textarea_selectors:
                try:
                    # CSS 선택자 사용
                    if selector.startswith("//"):
                        elements = driver.find_elements(By.XPATH, selector)
                    else:
                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for el in elements:
                        if el.is_displayed() and el.is_enabled():
                            text_area = el
                            print(f"[click_and_submit_comment] 텍스트 영역 발견: {selector}")
                            break
                    
                    if text_area:
                        break
                except Exception as e:
                    continue
            
            if not text_area:
                raise Exception("텍스트 입력 영역을 찾을 수 없음")
            
            # 5. 텍스트 영역 초기화 및 입력 개선
            try:
                # 먼저 클릭하여 포커스
                driver.execute_script("arguments[0].click();", text_area)
                time.sleep(0.5)
                
                # 기존 텍스트 선택 및 삭제
                driver.execute_script("arguments[0].select();", text_area)
                text_area.send_keys(Keys.DELETE)
                time.sleep(0.5)
                
                # JS로 직접 값 설정 먼저 시도
                driver.execute_script("arguments[0].value = arguments[1];", text_area, ai_reply_text)
                time.sleep(0.5)
                
                # 이벤트 발생 시뮬레이션 (React 입력 필드 인식용)
                driver.execute_script("""
                    var element = arguments[0];
                    var text = arguments[1];
                    
                    // 입력 이벤트 트리거
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                """, text_area, ai_reply_text)
                
                # 입력 내용 확인
                current_value = driver.execute_script("return arguments[0].value;", text_area)
                if not current_value or len(current_value) < 10:  # JS 입력이 실패한 경우
                    # 기존 방식으로 문자 단위 입력
                    text_area.clear()
                    for char in ai_reply_text:
                        text_area.send_keys(char)
                        time.sleep(0.01)
                
                print("[click_and_submit_comment] 텍스트 입력 완료")
                time.sleep(1)
            except Exception as e:
                print(f"[click_and_submit_comment] 텍스트 입력 중 오류: {str(e)}")
                raise Exception(f"텍스트 입력 실패: {str(e)}")
            
            # 6. 등록 버튼 찾기 - 제공된 HTML 구조 활용
            submit_btn = None
            submit_selectors = [
                # 제공된 HTML 구조 기반
                "//span[.//p[text()='등록']]",
                "//button[.//p[text()='등록']]",
                # 일반적인 선택자
                "//button[contains(., '등록')]",
                "//span[contains(., '등록')]",
                # 모든 버튼 검사
                "//button"
            ]
            
            for selector in submit_selectors:
                try:
                    elements = driver.find_elements(By.XPATH, selector)
                    for el in elements:
                        if '등록' in el.text and el.is_displayed():
                            # 요소의 클릭 가능 상태까지 올라가기
                            clickable_el = el
                            # 부모 요소가 button인지 확인
                            try:
                                parent = driver.execute_script("return arguments[0].parentNode;", el)
                                if parent.tag_name.lower() == 'button':
                                    clickable_el = parent
                            except:
                                # 실패하면 현재 요소 사용
                                pass
                            
                            submit_btn = clickable_el
                            print(f"[click_and_submit_comment] 등록 버튼 발견: '{el.text}'")
                            break
                    if submit_btn:
                        break
                except Exception as e:
                    continue
            
            if not submit_btn:
                raise Exception("등록 버튼을 찾을 수 없음")
            
            # 7. 등록 버튼 클릭 - 여러 방법 시도
            try:
                # JavaScript로 클릭
                driver.execute_script("arguments[0].click();", submit_btn)
                print("[click_and_submit_comment] 등록 버튼 JavaScript 클릭")
            except Exception as js_click_error:
                try:
                    # Action Chains로 클릭
                    from selenium.webdriver.common.action_chains import ActionChains
                    action = ActionChains(driver)
                    action.move_to_element(submit_btn).pause(0.5).click().perform()
                    print("[click_and_submit_comment] 등록 버튼 ActionChains 클릭")
                except Exception as action_click_error:
                    try:
                        # 일반 클릭
                        submit_btn.click()
                        print("[click_and_submit_comment] 등록 버튼 일반 클릭")
                    except Exception as e:
                        raise Exception(f"등록 버튼 클릭 실패: {str(e)}")
            
            # 8. 금지어 팝업 확인 개선 - 제공된 HTML 기반
            time.sleep(2)  # 팝업이 나타날 시간 확보
            popup_found = False
            
            try:
                # 알림 다이얼로그 찾기 (제공된 HTML 구조 활용)
                alert_selectors = [
                    # 제공된 HTML 기반
                    "//div[@role='alertdialog']",
                    "//div[contains(@class, 'Dialog_b_qmgb_3pnjmu4')]",
                    # 일반적인 팝업 선택자
                    "//div[@role='dialog']",
                    "//div[contains(@class, 'Alert')]"
                ]
                
                for selector in alert_selectors:
                    try:
                        popups = driver.find_elements(By.XPATH, selector)
                        for popup in popups:
                            if popup.is_displayed():
                                # 금지어 메시지 추출
                                error_msg = popup.text
                                print(f"[click_and_submit_comment] 팝업 감지: '{error_msg}'")
                                
                                # 금지어 키워드 추출 개선
                                # 예: "'쿠팡' 키워드는 입력하실 수 없습니다."
                                prohibited_match = re.search(r"'([^']+)'", error_msg)
                                if prohibited_match:
                                    prohibited_word = prohibited_match.group(1)
                                    detected_prohibited_words.add(prohibited_word)
                                    print(f"[click_and_submit_comment] 금지어 감지: '{prohibited_word}'")
                                
                                # 닉네임/작성자 관련 메시지인지 확인
                                if "닉네임" in error_msg or "작성자" in error_msg:
                                    is_author_prohibited = True
                                    print("[click_and_submit_comment] 작성자 이름 관련 금지어 감지")
                                
                                # 확인 버튼 찾기
                                for confirm_btn in popup.find_elements(By.XPATH, ".//button"):
                                    if "확인" in confirm_btn.text:
                                        driver.execute_script("arguments[0].click();", confirm_btn)
                                        print("[click_and_submit_comment] 팝업 확인 버튼 클릭")
                                        break
                                
                                popup_found = True
                                break
                        if popup_found:
                            break
                    except Exception as e:
                        continue
                
                if popup_found:
                    print(f"[click_and_submit_comment] 금지어 팝업 감지, 시도 {attempt}/{max_attempts}")
                    if attempt == max_attempts:
                        return False, detected_prohibited_words, is_author_prohibited
                    break  # 현재 시도 중단하고 다음 시도로
            except Exception as e:
                print(f"[click_and_submit_comment] 팝업 처리 중 오류: {str(e)}")
            
            # 팝업이 없으면 성공 (또는 미감지)
            if not popup_found:
                print(f"[click_and_submit_comment] 댓글 등록 성공 (시도 {attempt}/{max_attempts})")
                return True, set(), False
            
        except Exception as e:
            print(f"[click_and_submit_comment] 댓글 등록 시도 {attempt} 실패: {e}")
            if attempt == max_attempts:
                return False, detected_prohibited_words, is_author_prohibited
            time.sleep(1)
    
    # 모든 시도 실패
    return False, detected_prohibited_words, is_author_prohibited

###################################################
# 13) 리뷰 카드 처리 핵심 함수
###################################################

def parse_review_info(driver, card_element):
    """리뷰 카드에서 정보 추출 - 별점 파싱 알고리즘 강화"""
    try:
        # 0. 리뷰인지 추가 검증 (새로 추가)
        if not validate_review_card(card_element):
            print("[parse_review_info] 유효한 리뷰 카드가 아님")
            return {
                "author": "(유효하지 않음)",
                "rating": 0,
                "review_text": "",
                "order_menu": "",
                "delivery_review": "",
                "is_valid": False  # 새로 추가된 유효성 필드
            }
        
        # 나머지 코드는 기존과 동일하게 유지
        # 1. 작성자 추출
        author = "알 수 없음"
        author_selectors = [
            ".//div[contains(@class, 'Flex_c_qbca_bbdidap')]//span[contains(@class, 'Typography')][1]",
            ".//span[contains(@class, 'Typography_b_qmgb_1bisyd47')]",
            ".//span[contains(@class, 'Typography')]"
        ]
        
        for selector in author_selectors:
            try:
                elements = card_element.find_elements(By.XPATH, selector)
                for element in elements:
                    text = element.text.strip()
                    if text and len(text) > 0 and len(text) < 20:
                        author = text
                        break
                if author != "알 수 없음":
                    break
            except:
                continue
        
        # 2. 별점 추출 - 기존 코드 그대로 유지
        rating = 5
        try:
            js_script = """
            var stars = 0;
            try {
                var svgs = arguments[0].querySelectorAll('svg');
                for (var i = 0; i < svgs.length; i++) {
                    var paths = svgs[i].querySelectorAll('path[fill="#FFC600"]');
                    if (paths.length > 0) {
                        stars++;
                    }
                }
                stars = Math.min(stars, 5);
                
                if (stars === 0) {
                    var allPaths = arguments[0].querySelectorAll('svg path');
                    for (var j = 0; j < allPaths.length; j++) {
                        var fill = allPaths[j].getAttribute('fill');
                        if (fill && (fill.toLowerCase() === '#ffc600' || fill.toLowerCase() === 'gold' || fill.toLowerCase() === 'yellow')) {
                            stars++;
                        }
                    }
                    stars = Math.min(stars, 5);
                }
            } catch (e) {
                console.error("별점 계산 오류:", e);
            }
            
            return stars > 0 ? stars : 5;
            """
            detected_stars = driver.execute_script(js_script, card_element)
            if detected_stars > 0:
                rating = detected_stars
        except Exception as e:
            print(f"[parse_review_info] 별점 JavaScript 추출 오류: {str(e)}")
        
        # 3. 리뷰 텍스트 추출 - 기존 코드 그대로 유지
        review_text = ""
        review_selectors = [
            ".//span[contains(@class, 'Typography_b_qmgb_1bisyd49') and contains(@class, 'Typography_b_qmgb_1bisyd41u')]",
            ".//div[contains(@class, 'Flex_c_qbca_bbdidap')][2]//span[contains(@class, 'Typography')]"
        ]
        
        for selector in review_selectors:
            try:
                elements = card_element.find_elements(By.XPATH, selector)
                for element in elements:
                    text = element.text.strip()
                    if text and len(text) > 0 and text != author and not text.startswith('별점'):
                        if not text.startswith('[고기천원]'):
                            review_text = text
                            break
                if review_text:
                    break
            except:
                continue
        
        # 4. 주문 메뉴 추출 - 기존 코드 그대로 유지
        order_menu = ""
        try:
            menu_els = card_element.find_elements(By.XPATH, 
                ".//span[contains(@class, 'Badge_b_qmgb_19agxism')]")
            if menu_els:
                order_menu = ", ".join([m.text.strip() for m in menu_els if m.text.strip()])
        except Exception as e:
            print(f"[parse_review_info] 메뉴 추출 오류: {str(e)}")
        
        print(f"[parse_review_info] 추출 결과: 작성자={author}, 별점={rating}, 리뷰길이={len(review_text)}, 메뉴={order_menu}")
        
        # 리턴 값에 is_valid 필드 추가 (새로 추가)
        return {
            "author": author,
            "rating": rating,
            "review_text": review_text,
            "order_menu": order_menu,
            "delivery_review": "",
            "is_valid": True  # 새로 추가된 유효성 필드
        }
    
    except Exception as e:
        print(f"[parse_review_info] 리뷰 정보 파싱 오류: {str(e)}")
        return {
            "author": "(오류)",
            "rating": 0,  # 0으로 변경
            "review_text": "",
            "order_menu": "",
            "delivery_review": "",
            "is_valid": False  # 새로 추가된 유효성 필드
        }

def validate_review_card(card_element):
    """카드가 실제 리뷰인지 검증 - 조건 완화"""
    try:
        # 1. 빠른 체크: 명백히 UI 요소인 경우 제외
        excluded_texts = ["평균 별점", "고객이 보는 리뷰 정렬", "최신순", "사장님!", "배민마케팅"]
        for text in excluded_texts:
            if text in card_element.text:
                return False
        
        # 2. 리뷰 특성 체크 (하나라도 만족하면 리뷰로 간주)
        
        # 별점 체크 - 더 포괄적인 방식으로
        has_stars = False
        try:
            # 노란색 별 확인 (일반적인 방법)
            gold_paths = card_element.find_elements(By.XPATH, ".//path[@fill='#FFC600']")
            if 0 < len(gold_paths) <= 5:
                has_stars = True
                
            # 별점 텍스트 확인 (대체 방법)
            if not has_stars and "별점" in card_element.text:
                has_stars = True
        except:
            pass
            
        # 버튼 체크 - 댓글 버튼이 있으면 리뷰일 가능성 높음
        has_comment_btn = False
        try:
            buttons = card_element.find_elements(By.TAG_NAME, "button")
            for btn in buttons:
                if '사장님' in btn.text or '댓글' in btn.text or '답글' in btn.text:
                    has_comment_btn = True
                    break
        except:
            pass
        
        # 리뷰 내용 체크 - 리뷰 내용을 포함하는 요소가 있으면 리뷰일 가능성 높음
        has_review_content = False
        try:
            # Typography 클래스를 가진 텍스트 요소 확인
            typography_elements = card_element.find_elements(By.XPATH, ".//span[contains(@class, 'Typography')]")
            for element in typography_elements:
                text = element.text.strip()
                # 텍스트 길이가 충분히 길면 리뷰 내용일 가능성 높음
                if len(text) > 10:
                    has_review_content = True
                    break
        except:
            pass
            
        # 리뷰 메뉴 확인
        has_menu = False
        try:
            menu_elements = card_element.find_elements(By.XPATH, ".//span[contains(@class, 'Badge')]")
            if menu_elements:
                has_menu = True
        except:
            pass
            
        # 조합 조건: 하나 이상의 리뷰 특성이 있으면 리뷰로 간주
        return has_stars or has_comment_btn or has_review_content or has_menu
        
    except Exception as e:
        print(f"[validate_review_card] 검증 중 오류: {str(e)}")
        # 오류 발생 시 안전하게 True 반환 (처리 시도)
        return True

def find_reply_button(card_element):
    """개선된 답글 버튼 찾기 함수"""
    try:
        # 시도할 선택자 목록 확장
        selectors = [
            # 1. 버튼 안의 텍스트로 찾기
            ".//button[contains(., '사장님 댓글')]",
            ".//button[contains(., '사장님답글')]",
            ".//button[.//span[contains(text(), '사장님')]]",
            # 2. 클래스 기반 셀렉터
            ".//div[contains(@class, 'CEOCommentCreator-module')]//button",
            # 3. 추가 선택자
            ".//button[contains(@class, 'StyledButton')]",
            ".//button[@role='button']"
        ]
        
        for selector in selectors:
            try:
                buttons = card_element.find_elements(By.XPATH, selector)
                for button in buttons:
                    if '사장님' in button.text or button.get_attribute("aria-label") == "사장님 댓글":
                        return button
            except:
                continue
            
        # 모든 버튼 검색 (마지막 방법)
        try:
            all_buttons = card_element.find_elements(By.TAG_NAME, "button")
            for button in all_buttons:
                try:
                    if ('사장님' in button.text or 
                        '답글' in button.text or 
                        '댓글' in button.text or
                        button.get_attribute("aria-label") == "사장님 댓글"):
                        return button
                except:
                    pass
        except:
            pass
            
        return None
        
    except Exception as e:
        print(f"[find_reply_button] 답글 버튼 찾기 오류: {str(e)}")
        return None

def handle_review_card(driver, store_code, platform_name, platform_code, store_nm, card_element, rule):
    """개선된 리뷰 카드 처리 함수"""
    # 1. 30일 경과 체크
    try:
        old_notice_els = card_element.find_elements(By.XPATH, ".//*[contains(text(), '30일이 지난 리뷰')]")
        if old_notice_els:
            print("[handle_review_card] 30일 지난 리뷰 -> 처리 중단")
            return "STOP_30D"
    except:
        pass

    # 2. 답글 버튼 체크
    reply_btn = find_reply_button(card_element)
    if not reply_btn:
        print("[handle_review_card] 답글 불가능한 리뷰(버튼 없음)")
        return None

    # 3. 리뷰 정보 파싱
    review_info = parse_review_info(driver, card_element)
    author = review_info["author"]
    rating = review_info["rating"]
    review_text = review_info["review_text"]
    order_menu = review_info["order_menu"]
    delivery_review = review_info["delivery_review"]

    print(f"[handle_review_card] => 작성자={author}, 별점={rating}, 리뷰내용={review_text[:20]}...")

    # 4. 리뷰 해시 생성 및 날짜 처리
    review_hash = generate_review_hash(store_code, author, review_text)
    review_date = datetime.now().date().isoformat()
    
    # 네트워크 데이터 시도
    try:
        review_data = extract_review_data_from_network(driver, card_element)
        if review_data:
            review_hash = review_data['review_id']
            review_date = review_data['review_date']
            print(f"[handle_review_card] 네트워크에서 추출한 리뷰 ID: {review_hash}, 날짜: {review_date}")
    except Exception as e:
        print(f"[handle_review_card] 네트워크 데이터 추출 실패: {str(e)}")
        
    # HTML에서 날짜 추출 시도
    if review_date == datetime.now().date().isoformat():
        try:
            html_date = extract_relative_date(driver, card_element)
            if html_date:
                review_date = html_date
                print(f"[handle_review_card] HTML에서 날짜 추출: {html_date}")
        except Exception as e:
            print(f"[handle_review_card] HTML 날짜 추출 실패: {str(e)}")

    # 5. 기존 리뷰 정보 확인
    existing = supabase.table("reviews").select("*").eq("review_id", review_hash).execute()

    now = datetime.now()
    current_date = now.date().isoformat()

    # 리뷰 날짜 기반 경과일 계산
    try:
        review_date_obj = datetime.fromisoformat(review_date).date()
        today = now.date()
        days_since_review = (today - review_date_obj).days
        print(f"[handle_review_card] 리뷰 날짜: {review_date}, 경과일: {days_since_review}일")
    except Exception as e:
        print(f"[handle_review_card] 날짜 계산 오류: {str(e)}")
        days_since_review = 0  # 안전하게 기본값 설정

    # 기존 리뷰가 있는 경우
    if existing.data:
        record = existing.data[0]
        # 날짜가 이미 설정되어 있으면 유지 (하지만 경과일은 새로 계산된 값 사용)
        review_date = record.get('review_date', review_date)
        status = record.get('response_status', '')
        retry_count = record.get('retry_count', 0)
        
        # 재시도 횟수 체크 (5회 초과시 스킵)
        if retry_count >= 10:
            print(f"[handle_review_card] 최대 재시도 횟수 초과: {retry_count}/10")
            return None
        
        # "답변완료" 상태면 스킵
        if status == "답변완료":
            # 기존 답변 정보 가져오기
            existing_reply = record.get('ai_response', '')
            
            # 에러로그에 기록 (불일치 상황)
            print(f"[handle_review_card] 이미 답변완료된 리뷰이지만 미답변 탭에 존재: {review_hash[:8]} - 재답변 시도")
            save_error_log_to_supabase(
                category="데이터 불일치",
                platform=platform_name,
                store_code=store_code,
                error_type="답변완료 리뷰가 미답변 탭에 존재",
                error_message=f"리뷰 ID: {review_hash}, 작성자: {author}, 별점: {rating}, 상태: {status}, 경과일: {days_since_review}일",
                stack_trace=f"리뷰 내용: {review_text[:100]}... | 기존 답변: {existing_reply[:100]}..."
            )
            
            # 중요: 재처리를 위해 return하지 않고 계속 진행 (기존 코드는 여기서 return None으로 중단)
            # 재시도 횟수 증가
            retry_count += 1
        
        # 사장님 확인 필요 리뷰에 대한 처리
        if status == "사장님 확인필요":
            # 2일이 지난 후에만 처리
            if days_since_review < 2:
                print(f"[handle_review_card] 사장님 확인필요 리뷰 스킵 (경과일: {days_since_review}일, 2일 이후 처리 예정)")
                return None
            else:
                print(f"[handle_review_card] 사장님 확인필요 리뷰 처리 시작 (경과일: {days_since_review}일)")
        
        # 일반 리뷰는 1일 대기 (별점에 관계없이 1일 후에 처리)
        elif status != "답변완료" and days_since_review < 1:
            print(f"[handle_review_card] 리뷰 작성 후 {days_since_review}일 경과 - 답변 대기 (1일 후 응답 예정)")
            _save_review_data(
                store_code=store_code,
                platform=platform_name,
                platform_code=platform_code,
                review_name=author,
                rating=rating,
                ordered_menu=order_menu,
                delivery_review=delivery_review,
                review_content=review_text,
                ai_response="",
                review_date=review_date,
                status="답변대기",
                store_name=store_nm,
                review_id=review_hash,
                retry_count=retry_count
            )
            return None
    else:
        retry_count = 0
        
        # 리뷰 분석으로 사장님 확인 필요 여부 판단
        is_empty_review = not review_text or review_text.strip() == "" or review_text.strip() == "[고기천원]마라탕麻"
        if not is_empty_review or rating < 4:
            analysis_result = analyze_restaurant_review(review_text, rating, order_menu, delivery_review)
            
            # AI 답변 불가능 판정이면 사장님 확인 필요로 저장
            if not analysis_result['ai_reply'] and not (is_empty_review and rating >= 4):
                print(f"[handle_review_card] AI 답변 불가 판정: {analysis_result['reason']}")
                _save_review_data(
                    store_code=store_code,
                    platform=platform_name,
                    platform_code=platform_code,
                    review_name=author,
                    rating=rating,
                    ordered_menu=order_menu,
                    delivery_review=delivery_review,
                    review_content=review_text,
                    ai_response="",
                    review_date=review_date,
                    status="사장님 확인필요",
                    store_name=store_nm,
                    review_id=review_hash,
                    boss_reply_needed=True,
                    review_category=analysis_result.get('category', ''),
                    review_reason=analysis_result.get('reason', ''),
                    retry_count=0
                )
                return None
        
        # 경과일 기반 처리 결정 (1일 미만은 대기, 1일 이상은 처리)
        if days_since_review < 1:
            print(f"[handle_review_card] 신규 리뷰 발견 - {days_since_review}일 경과, 답변 대기 상태로 저장")
            _save_review_data(
                store_code=store_code,
                platform=platform_name,
                platform_code=platform_code,
                review_name=author,
                rating=rating,
                ordered_menu=order_menu,
                delivery_review=delivery_review,
                review_content=review_text,
                ai_response="",
                review_date=review_date,
                status="답변대기",
                store_name=store_nm,
                review_id=review_hash,
                retry_count=0
            )
            return None
        else:
            print(f"[handle_review_card] {days_since_review}일 경과한 리뷰 - 바로 처리 진행")

    # 6. 별점 정책 체크
    rating_key = f"rating_{rating}_reply"
    if rating < 1 or rating > 5 or not rule.get(rating_key, True):
        print(f"[handle_review_card] 별점({rating}) 자동답글 제외")
        _save_review_data(
            store_code=store_code,
            platform=platform_name,
            platform_code=platform_code,
            review_name=author,
            rating=rating,
            ordered_menu=order_menu,
            delivery_review=delivery_review,
            review_content=review_text,
            ai_response="",
            review_date=review_date,
            status="미답변",
            store_name=store_nm,
            review_id=review_hash,
            retry_count=retry_count
        )
        return None
    
    # 7. 내용 없는 리뷰는 내용 생성
    is_empty_review = not review_text or review_text.strip() == "" or review_text.strip() == "[고기천원]마라탕麻"
    if is_empty_review:
        if rating >= 4:
            # 긍정적인 빈 리뷰는 자동 생성
            review_text = "맛있게 잘 먹었습니다"
            print(f"[handle_review_card] 내용 없는 고평점 리뷰에 기본 내용 추가: '{review_text}'")
        else:
            # 부정적인 빈 리뷰는 사장님 확인 필요
            print(f"[handle_review_card] 내용 없는 저평점 리뷰는 사장님 확인 필요")
            _save_review_data(
                store_code=store_code,
                platform=platform_name,
                platform_code=platform_code,
                review_name=author,
                rating=rating,
                ordered_menu=order_menu,
                delivery_review=delivery_review,
                review_content=review_text,
                ai_response="",
                review_date=review_date,
                status="사장님 확인필요",
                store_name=store_nm,
                review_id=review_hash,
                boss_reply_needed=True,
                review_category="빈 리뷰",
                review_reason="내용이 없는 저평점 리뷰",
                retry_count=retry_count
            )
            return None

    # 8. AI 답변 생성
    store_info = {
        "store_code": store_code,
        "greeting_start": rule.get("greeting_start", "안녕하세요"),
        "greeting_end": rule.get("greeting_end", "감사합니다"),
        "role": rule.get("role", ""),
        "tone": rule.get("tone", ""),
        "max_length": rule.get("max_length", 300),
        "author": author,
        "rating": rating
    }
    
    try:
        ai_reply = generate_ai_reply(review_text, store_info)
        if not ai_reply:
            raise Exception("AI 답변 생성 실패")
        
        # 9. 답변 품질 검사
        is_good_reply, _ = score_reply(ai_reply, review_text, author, rating, threshold=80)
        if not is_good_reply:
            print("[handle_review_card] AI 답변 품질 미달")
            # 다시 시도
            ai_reply = generate_ai_reply(review_text, store_info)
            if not ai_reply:
                raise Exception("AI 답변 재생성 실패")
                
            # 두 번째 생성도 실패하면 별도 처리
            is_good_reply, _ = score_reply(ai_reply, review_text, author, rating, threshold=75)
            if not is_good_reply:
                print("[handle_review_card] 두 번째 AI 답변도 품질 미달")
        
        # 10. 댓글 등록
        success, prohibited_words, is_author_prohibited = click_and_submit_comment(
            driver, card_element, ai_reply, review_text, store_info)
        
        # 금지어 감지 시 재시도
        max_retries = 2
        retry_count_prohibited = 0
        
        while not success and retry_count_prohibited < max_retries:
            retry_count_prohibited += 1
            print(f"[handle_review_card] 금지어로 인한 댓글 등록 실패, 재시도 {retry_count_prohibited}/{max_retries}")
            
            # 작성자 이름 변경 (필요한 경우)
            if is_author_prohibited:
                print("[handle_review_card] 작성자 이름을 '고객님'으로 변경")
                store_info["author"] = "고객님"
            
            # 금지어를 피한 새 AI 답변 생성
            avoid_prompt = f"다음 단어를 사용하지 마세요: {', '.join(prohibited_words)}" if prohibited_words else ""
            store_info["avoid_words"] = avoid_prompt
            
            # 새 AI 답변 생성
            ai_reply = generate_ai_reply(review_text, store_info)
            if not ai_reply:
                raise Exception("금지어 회피 AI 답변 생성 실패")
            
            # 답변 검증
            is_valid, reason = validate_reply_content(ai_reply)
            if not is_valid:
                # 다시 시도
                ai_reply = generate_ai_reply(review_text, store_info)
                if not ai_reply:
                    raise Exception("금지어 회피 AI 답변 재생성 실패")
                    
                is_valid, reason = validate_reply_content(ai_reply)
                if not is_valid:
                    raise Exception(f"답변 검증 실패: {reason}")
            
            # 작성자명 강제 변경 (이름에 금지어가 있는 경우)
            if is_author_prohibited:
                # 'OOO님' 패턴을 '고객님'으로 변경
                ai_reply = re.sub(r'[가-힣\w]+님', '고객님', ai_reply)
            
            # 변경된 AI 답변으로 다시 시도
            success, prohibited_words, is_author_prohibited = click_and_submit_comment(
                driver, card_element, ai_reply, review_text, store_info)
        
        # 최종 결과 처리
        if not success:
            raise Exception(f"금지어 회피 최대 시도 횟수({max_retries}) 초과")

        # 성공 처리
        print("[handle_review_card] 댓글 등록 성공!")
        _save_review_data(
            store_code=store_code,
            platform=platform_name,
            platform_code=platform_code,
            review_name=author,
            rating=rating,
            ordered_menu=order_menu,
            delivery_review=delivery_review,
            review_content=review_text,
            ai_response=ai_reply,
            review_date=review_date,
            status="답변완료",
            store_name=store_nm,
            review_id=review_hash,
            retry_count=retry_count + 1
        )
        return True

    except Exception as e:
        error_msg = str(e)
        print(f"[handle_review_card] 댓글 등록 실패: {error_msg}")
        
        save_error_screenshot(driver, store_code, "댓글등록실패")
        save_error_log_to_supabase(
            category="답글 실패",
            platform=platform_name,
            store_code=store_code,
            error_type="댓글 등록 실패",
            error_message=error_msg
        )
        
        _save_review_data(
            store_code=store_code,
            platform=platform_name,
            platform_code=platform_code,
            review_name=author,
            rating=rating,
            ordered_menu=order_menu,
            delivery_review=delivery_review,
            review_content=review_text,
            ai_response="",
            review_date=review_date,
            status="실패",
            store_name=store_nm,
            review_id=review_hash,
            retry_count=retry_count + 1
        )
        return None
    
def navigate_to_uncommented_tab(driver, store_code):
    """미답변 탭으로 이동 - 클릭 이후 상태 확인 개선"""
    max_attempts = 2
    
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"[navigate_to_uncommented_tab] 미답변 탭 클릭 시도 {attempt}/{max_attempts}")
            
            # 페이지 로딩 대기
            time.sleep(3)
            
            # 미답변 탭 요소 찾기
            tab_element = None
            tab_selectors = [
                "//button[@id='no-comment' and @role='tab']",
                "//button[@role='tab' and contains(., '미답변')]",
                "//button[contains(@class, 'Tab_b_qmgb_sx92a1t') and contains(., '미답변')]"
            ]
            
            for selector in tab_selectors:
                try:
                    tab_element = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    if tab_element:
                        print(f"[navigate_to_uncommented_tab] 미답변 탭 요소 찾음: {selector}")
                        break
                except:
                    continue
            
            if not tab_element:
                print("[navigate_to_uncommented_tab] 미답변 탭 요소를 찾을 수 없음")
                return False
            
            # 클릭 전 상태 확인
            pre_click_selected = tab_element.get_attribute("aria-selected")
            print(f"[navigate_to_uncommented_tab] 클릭 전 상태: aria-selected={pre_click_selected}")
            
            # 이미 선택된 상태면 컨텐츠 확인만 진행
            if pre_click_selected == "true":
                print("[navigate_to_uncommented_tab] 탭이 이미 선택되어 있음")
            else:
                # 클릭 시도 - 여러 방법 순차적으로 시도
                click_methods = [
                    # 1. JavaScript로 클릭
                    lambda: driver.execute_script("arguments[0].click();", tab_element),
                    # 2. ActionChains 사용
                    lambda: ActionChains(driver).move_to_element(tab_element).click().perform(),
                    # 3. 기본 클릭
                    lambda: tab_element.click()
                ]
                
                click_success = False
                for click_index, click_method in enumerate(click_methods):
                    try:
                        print(f"[navigate_to_uncommented_tab] 클릭 방법 {click_index + 1} 시도")
                        click_method()
                        time.sleep(2)
                        
                        # 클릭 후 상태 확인 - 실제로 탭이 선택되었는지 검증
                        current_element = driver.find_element(By.XPATH, "//button[@id='no-comment']")
                        if current_element.get_attribute("aria-selected") == "true":
                            click_success = True
                            print("[navigate_to_uncommented_tab] 탭 선택 확인됨 (aria-selected=true)")
                            break
                    except Exception as e:
                        print(f"[navigate_to_uncommented_tab] 클릭 방법 {click_index + 1} 실패: {str(e)}")
                        
                if not click_success:
                    if attempt < max_attempts:
                        print("[navigate_to_uncommented_tab] 모든 클릭 방법 실패, 재시도")
                        continue
                    else:
                        print("[navigate_to_uncommented_tab] 탭 선택 실패")
                        save_error_screenshot(driver, store_code, "TabSelectionFail")
                        return False
            
            # 클릭 후 충분한 대기 시간 추가
            time.sleep(3)
            
            # 클릭 후 리뷰 확인
            review_cards = find_review_cards(driver)
            
            print(f"[navigate_to_uncommented_tab] {store_code} 미답변 탭 이동 성공, 리뷰 카드 수: {len(review_cards)}")
            return True
                
        except Exception as e:
            print(f"[navigate_to_uncommented_tab] {attempt}번째 시도 중 예외 발생: {str(e)}")
            
            if attempt < max_attempts:
                time.sleep(2)
            else:
                print(f"[navigate_to_uncommented_tab] {store_code} 미답변 탭 이동 실패")
                save_error_screenshot(driver, store_code, "TabNavigationFail")
                return False
    
    return False

def find_review_cards(driver):
    """효율적인 리뷰 카드 찾기 - 중복 검색 제거"""
    # 정확한 리뷰 카드 선택자 사용 (가장 신뢰할 수 있는 것만)
    primary_selectors = [
        "div[class*='ReviewContent-module']", 
        "div.Container_c_qbca_1utdzds5.ReviewContent-module__Ksg4"
    ]
    
    # 먼저 정확한 선택자로 시도
    for selector in primary_selectors:
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, selector)
            if cards:
                # 빠른 필터링 (명확한 기준만 적용)
                real_reviews = []
                for card in cards:
                    if not is_element_valid(card):
                        continue
                        
                    # 별점 빠르게 확인
                    stars = card.find_elements(By.XPATH, ".//path[@fill='#FFC600']")
                    has_stars = 0 < len(stars) <= 5
                    
                    # 댓글 버튼 빠르게 확인
                    has_comment_btn = len(card.find_elements(By.XPATH, ".//button[contains(., '사장님 댓글')]")) > 0
                    
                    if has_stars or has_comment_btn:
                        real_reviews.append(card)
                        
                if real_reviews:
                    print(f"[find_review_cards] 선택자 '{selector}'로 {len(real_reviews)}개 리뷰 찾음")
                    return real_reviews
        except Exception as e:
            print(f"[find_review_cards] {selector} 검색 중 오류: {str(e)}")
            continue
    
    # 더 넓은 범위의 선택자로 시도
    fallback_selector = "div[data-atelier-component='Container']"
    try:
        containers = driver.find_elements(By.CSS_SELECTOR, fallback_selector)
        real_reviews = []
        
        for container in containers:
            try:
                if not is_element_valid(container):
                    continue
                    
                # 명확한 리뷰 특성 확인
                stars = container.find_elements(By.XPATH, ".//path[@fill='#FFC600']")
                has_stars = 0 < len(stars) <= 5
                
                # 사장님 댓글 버튼 확인
                has_comment_btn = len(container.find_elements(
                    By.XPATH, ".//button[contains(., '사장님 댓글') or contains(., '댓글')]"
                )) > 0
                
                # 통계/UI 요소 제외
                is_ui_element = False
                ui_texts = ["평균 별점", "최근 6개월", "기준", "사장님!", "배민마케팅"]
                for text in ui_texts:
                    if len(container.find_elements(By.XPATH, f".//*[contains(text(), '{text}')]")) > 0:
                        is_ui_element = True
                        break
                
                if (has_stars or has_comment_btn) and not is_ui_element:
                    real_reviews.append(container)
                    
            except:
                continue
                
        print(f"[find_review_cards] 대체 선택자로 {len(real_reviews)}개 리뷰 찾음")
        return real_reviews
        
    except Exception as e:
        print(f"[find_review_cards] 리뷰 카드 찾기 오류: {str(e)}")
        return []

###################################################
# 14) 리뷰 페이지 전체 처리 함수
###################################################
def process_reviews_on_page_improved(driver, store_code, platform_name, platform_code, store_nm, rule):
    """개선된 리뷰 페이지 처리 함수 - 30일 지난 리뷰 로직 개선"""
    global processed_reviews_in_session
    processed_count = 0
    
    # 1. 이미 처리한 리뷰 ID 추적 (ID로 추적하여 중복 방지)
    processed_ids = set()
    
    # 2. 페이지 전체 높이 확인
    total_height = driver.execute_script("return document.body.scrollHeight")
    
    # 3. 각 스크롤마다 이동할 높이 설정 (화면 높이의 2/3 정도)
    viewport_height = driver.execute_script("return window.innerHeight")
    scroll_height = int(viewport_height * 0.67)
    
    # 4. 현재 스크롤 위치 초기화
    current_position = 0
    
    # 5. 이전 높이와 반복 횟수 추적 
    last_height = total_height
    no_new_content_count = 0
    
    # 6. 30일 지난 리뷰 발견 플래그 (다음 스크롤부터 중단)
    found_old_review = False
    
    print(f"[process_reviews_on_page_improved] 시작: 총 높이={total_height}, 스크롤 단위={scroll_height}")
    
    while current_position < total_height and no_new_content_count < 3:
        # 이전 스크롤에서 30일 지난 리뷰 발견했으면 처리 중단
        if found_old_review:
            print("[process_reviews_on_page_improved] 이전 스크롤에서 30일 지난 리뷰 발견, 이후 처리 중단")
            break
        
        # A. 스크롤 다운
        driver.execute_script(f"window.scrollTo(0, {current_position});")
        time.sleep(1.5)  # 로딩 대기
        
        # B. 현재 화면에 보이는 리뷰 찾기 (JavaScript 활용)
        visible_cards = driver.execute_script("""
            const allCards = document.querySelectorAll('div[class*="ReviewContent-module"]');
            return Array.from(allCards).filter(card => {
                const rect = card.getBoundingClientRect();
                // 현재 화면에 완전히 또는 부분적으로 보이는 카드 선택
                return (rect.top < window.innerHeight && rect.bottom > 0);
            });
        """)
        
        if visible_cards:
            print(f"[process_reviews_on_page_improved] 현재 위치({current_position})에서 {len(visible_cards)}개 카드 발견")
        
        # C. 발견한 카드 처리
        cards_old_review_found = False  # 현재 스크롤에서 30일 지난 리뷰 발견 플래그
        
        for card in visible_cards:
            if not is_element_valid(card):
                continue
                
            # 카드의 리뷰 정보 추출
            review_info = parse_review_info(driver, card)
            if not review_info.get("is_valid", False):
                continue
                
            # 리뷰 ID 생성 (중복 체크용)
            author = review_info["author"]
            review_text = review_info["review_text"]
            review_hash = generate_review_hash(store_code, author, review_text)
            
            # 이미 처리한 리뷰 스킵 (세션 전체 + 현재 함수 호출 내)
            if review_hash in processed_reviews_in_session or review_hash in processed_ids:
                continue
                
            # 리뷰 처리 
            try:
                # 카드가 잘 보이도록 스크롤 조정
                driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", card)
                time.sleep(0.5)
                
                # 리뷰 처리
                result = handle_review_card(driver, store_code, platform_name, platform_code, store_nm, card, rule)
                
                # 중요 변경: 30일 지난 리뷰 발견 시 현재 스크롤의 나머지 리뷰는 처리하고
                # 다음 스크롤부터 처리 중단하도록 플래그 설정
                if result == "STOP_30D":
                    print("[process_reviews_on_page_improved] 30일 이상 지난 리뷰 발견, 다음 스크롤부터 처리 중단")
                    cards_old_review_found = True
                    # 현재 리뷰는 처리 완료된 리뷰로 추가
                    processed_ids.add(review_hash)
                    processed_reviews_in_session.add(review_hash)
                    continue  # 다음 카드 처리로 넘어감 (현재 스크롤의 카드들은 계속 처리)
                
                if result:
                    processed_count += 1
                    
                # 처리 완료된 리뷰 목록에 추가
                processed_ids.add(review_hash)
                processed_reviews_in_session.add(review_hash)
            except Exception as e:
                print(f"[process_reviews_on_page_improved] 카드 처리 중 오류: {str(e)}")
        
        # 현재 스크롤에서 30일 지난 리뷰 발견 시 다음 스크롤부터 처리 중단 플래그 설정
        if cards_old_review_found:
            found_old_review = True
            print("[process_reviews_on_page_improved] 현재 스크롤의 모든 리뷰 처리 완료, 다음 스크롤은 건너뜁니다")
            # 루프는 계속해서 다음 스크롤로 이동하지만, 맨 위의 found_old_review 체크에서 루프 종료
        
        # D. 다음 위치로 이동
        current_position += scroll_height
        
        # E. 페이지 높이 변경 확인
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height > last_height:
            # 새 컨텐츠 로딩됨
            print(f"[process_reviews_on_page_improved] 새 컨텐츠 감지: {last_height} -> {new_height}")
            last_height = new_height
            total_height = new_height
            no_new_content_count = 0
        else:
            # 새 컨텐츠가 없음
            no_new_content_count += 1
            print(f"[process_reviews_on_page_improved] 새 컨텐츠 없음 카운트: {no_new_content_count}/3")
    
    print(f"[process_reviews_on_page_improved] 완료: 총 {processed_count}개 리뷰 처리")
    return processed_count

def find_review_cards_with_data_index(driver):
    """data-index 속성이 있는 리뷰 카드를 찾고 해당 속성을 반환하는 함수"""
    try:
        # 모든 카드 찾기
        all_cards = find_review_cards(driver)
        
        # 결과 저장 딕셔너리: {data-index: card_element}
        cards_with_index = {}
        
        # 각 카드에서 data-index 속성 확인
        for card in all_cards:
            try:
                # 상위 요소를 찾아 올라가며 data-index 속성 찾기
                parent = card
                max_depth = 5  # 최대 상위 5단계까지만 확인
                
                for _ in range(max_depth):
                    try:
                        data_index = parent.get_attribute("data-index")
                        if data_index is not None:
                            cards_with_index[data_index] = card
                            print(f"[find_review_cards_with_data_index] data-index={data_index} 리뷰 카드 발견")
                            break
                    except:
                        pass
                    
                    # 부모 요소로 이동
                    parent = driver.execute_script("return arguments[0].parentNode;", parent)
                    if parent is None:
                        break
                
            except Exception as e:
                continue
        
        print(f"[find_review_cards_with_data_index] 총 {len(cards_with_index)}개 리뷰(data-index 기준) 발견")
        return cards_with_index
        
    except Exception as e:
        print(f"[find_review_cards_with_data_index] 리뷰 카드 찾기 오류: {str(e)}")
        return {}

def process_reviews_by_data_index(driver, store_code, platform_name, platform_code, store_nm, rule):
    """간소화된 data-index 속성을 기반으로 리뷰 순차 처리 - 무한 루프 방지"""
    global processed_reviews_in_session
    processed_count = 0
    visited_review_hashes = set()  # 리뷰 해시 기반으로 중복 추적
    index_processed_hashes = {}    # 각 인덱스별로 처리한 해시값 {인덱스: set(해시값들)}
    max_attempts = 15              # 최대 시도 횟수 감소 (200 -> 15)
    
    # 처리할 대상 인덱스 - 항상 같은 인덱스를 유지하다가 "처리 불가" 상태일 때만 증가
    current_target_index = None
    
    # 리뷰가 없는 경우 연속 확인 횟수
    no_reviews_count = 0
    max_no_reviews = 3  # 리뷰가 없는 상태가 3번 연속 발생하면 종료
    
    # 전체 시도 루프
    for attempt in range(max_attempts):
        print(f"[process_reviews_by_data_index] 시도 {attempt+1}/{max_attempts}")
        
        # 현재 보이는 모든 리뷰 카드 찾기
        cards_with_index = find_review_cards_with_data_index(driver)
        
        if not cards_with_index:
            print("[process_reviews_by_data_index] 처리할 리뷰가 더 이상 없음")
            no_reviews_count += 1
            
            # 리뷰가 없는 상태가 연속 3번 발생하면 처리 중단
            if no_reviews_count >= max_no_reviews:
                print(f"[process_reviews_by_data_index] 리뷰가 {max_no_reviews}번 연속으로 없음, 처리 종료")
                return processed_count
                
            # 스크롤을 내려서 더 많은 리뷰 로드 시도
            driver.execute_script(f"window.scrollBy(0, 1000);")
            time.sleep(3)
            continue
        else:
            # 리뷰가 발견되면 카운트 초기화
            no_reviews_count = 0
            
        # 정렬된 인덱스 목록 가져오기
        try:
            sorted_indices = sorted(cards_with_index.keys(), key=lambda x: int(x) if x.isdigit() else float('inf'))
        except:
            sorted_indices = sorted(cards_with_index.keys())
            
        if not sorted_indices:
            print("[process_reviews_by_data_index] 정렬된 인덱스가 없음, 다음 시도로 진행")
            continue
            
        print(f"[process_reviews_by_data_index] 정렬된 data-index: {sorted_indices}")
        
        # 현재 타겟 인덱스가 없으면 가장 작은 인덱스 선택
        if current_target_index is None:
            current_target_index = sorted_indices[0]
            print(f"[process_reviews_by_data_index] 초기 타겟 인덱스 {current_target_index} 선택")
        
        # 타겟 인덱스가 화면에 없으면 스크롤 또는 다음 가능한 인덱스 선택
        if current_target_index not in cards_with_index:
            # 현재 표시된 인덱스 중 타겟보다 큰 인덱스 찾기
            next_indices = [idx for idx in sorted_indices if int(idx) > int(current_target_index)]
            if next_indices:
                current_target_index = next_indices[0]
                print(f"[process_reviews_by_data_index] 타겟 인덱스가 없어 다음 인덱스 {current_target_index} 선택")
            else:
                # 더 큰 인덱스가 없으면 스크롤해서 새 리뷰 로드
                driver.execute_script(f"window.scrollBy(0, 800);")
                time.sleep(2)
                print("[process_reviews_by_data_index] 더 큰 인덱스가 없어 스크롤 다운")
                continue
        
        # 현재 타겟 인덱스의 카드 가져오기
        card = cards_with_index[current_target_index]
        
        print(f"[process_reviews_by_data_index] data-index {current_target_index} 처리 시작")
        
        # 리뷰 정보 추출
        review_info = parse_review_info(driver, card)
        if not review_info.get("is_valid", False):
            print(f"[process_reviews_by_data_index] data-index {current_target_index}는 유효한 리뷰가 아님")
            # 다음 인덱스로 이동 (유효하지 않은 경우)
            next_indices = [idx for idx in sorted_indices if int(idx) > int(current_target_index)]
            if next_indices:
                current_target_index = next_indices[0]
            continue
        
        # 리뷰 해시 생성
        author = review_info["author"]
        review_text = review_info["review_text"]
        review_hash = generate_review_hash(store_code, author, review_text)
        
        # ===== 무한 루프 방지 로직 =====
        # 현재 인덱스에서 이미 처리한 해시 확인
        if current_target_index in index_processed_hashes:
            processed_hashes = index_processed_hashes[current_target_index]
            if review_hash in processed_hashes:
                print(f"[process_reviews_by_data_index] 인덱스 {current_target_index}에서 이미 처리한 리뷰 (해시: {review_hash[:8]})")
                # 이 인덱스+해시 조합이 이미 처리됐으면 다음 인덱스로 이동
                next_indices = [idx for idx in sorted_indices if int(idx) > int(current_target_index)]
                if next_indices:
                    current_target_index = next_indices[0]
                    print(f"[process_reviews_by_data_index] 다음 인덱스 {current_target_index}로 이동")
                else:
                    print("[process_reviews_by_data_index] 다음 인덱스가 없어 처리 종료")
                    break
                continue
        
        # 전체적으로 이미 처리한 리뷰인지 체크 (추가 안전 장치)
        if review_hash in visited_review_hashes:
            print(f"[process_reviews_by_data_index] 이미 처리한 리뷰 (해시: {review_hash[:8]})")
            # 인덱스별 처리 해시 집합에 추가
            if current_target_index not in index_processed_hashes:
                index_processed_hashes[current_target_index] = set()
            index_processed_hashes[current_target_index].add(review_hash)
            # 다음 인덱스로 이동
            next_indices = [idx for idx in sorted_indices if int(idx) > int(current_target_index)]
            if next_indices:
                current_target_index = next_indices[0]
                print(f"[process_reviews_by_data_index] 다음 인덱스 {current_target_index}로 이동")
            continue
        
        print(f"[process_reviews_by_data_index] data-index {current_target_index} 리뷰 처리 시도 (해시: {review_hash[:8]})")
        
        # 카드가 화면에 보이도록 스크롤
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
        time.sleep(1.5)
        
        try:
            # 리뷰 처리
            result = handle_review_card(driver, store_code, platform_name, platform_code, store_nm, card, rule)
            
            # 방문한 리뷰 해시 추가
            visited_review_hashes.add(review_hash)
            
            # 인덱스별 처리 해시 집합에 추가
            if current_target_index not in index_processed_hashes:
                index_processed_hashes[current_target_index] = set()
            index_processed_hashes[current_target_index].add(review_hash)
            
            if result == "STOP_30D":
                print(f"[process_reviews_by_data_index] 30일 경과 리뷰 발견, 처리 종료")
                return processed_count
            elif result == True:  # 답변 성공
                print(f"[process_reviews_by_data_index] data-index {current_target_index} 처리 성공")
                processed_count += 1
                
                # 핵심: 답변 성공 시 같은 인덱스 유지 (DOM 변경으로 새 리뷰가 당겨짐)
                print(f"[process_reviews_by_data_index] 답변 성공 - 같은 data-index {current_target_index} 유지")
                
                # 처리 후 잠시 대기
                time.sleep(2)
                
            else:  # 답변 대기, 사장님 확인 필요 등
                print(f"[process_reviews_by_data_index] data-index {current_target_index} - 처리 불가 상태")
                
                # 핵심: 처리 불가 시 다음 인덱스로 이동
                next_indices = [idx for idx in sorted_indices if int(idx) > int(current_target_index)]
                if next_indices:
                    current_target_index = next_indices[0]
                    print(f"[process_reviews_by_data_index] 처리 불가로 다음 인덱스 {current_target_index}로 이동")
                    
                # 짧은 대기
                time.sleep(1)
                
        except Exception as e:
            print(f"[process_reviews_by_data_index] data-index {current_target_index} 처리 중 오류: {str(e)}")
            visited_review_hashes.add(review_hash)  # 오류 발생해도 방문 처리
            
            # 이 인덱스의 처리 해시에 추가
            if current_target_index not in index_processed_hashes:
                index_processed_hashes[current_target_index] = set()
            index_processed_hashes[current_target_index].add(review_hash)
            
            # 오류 발생 시 다음 인덱스로 이동
            next_indices = [idx for idx in sorted_indices if int(idx) > int(current_target_index)]
            if next_indices:
                current_target_index = next_indices[0]
                print(f"[process_reviews_by_data_index] 오류 발생으로 다음 인덱스 {current_target_index}로 이동")
            
        # 각 시도 후 짧게 스크롤 다운 (모든 리뷰 유지 위해)
        driver.execute_script(f"window.scrollBy(0, 100);")
        time.sleep(1)
    
    # 처리 통계
    print(f"[process_reviews_by_data_index] 총 {processed_count}개 리뷰 처리 완료")
    
    # 미답변 탭 상태 요약 및 불일치 통계 
    completed_in_db_count = 0
    for review_hash in visited_review_hashes:
        try:
            # 리뷰 상태 확인
            status_check = supabase.table("reviews").select("response_status").eq("review_id", review_hash).execute()
            if status_check.data and status_check.data[0].get('response_status') == "답변완료":
                completed_in_db_count += 1
        except Exception as e:
            print(f"[process_reviews_by_data_index] 리뷰 상태 확인 오류: {str(e)}")
    
    # 불일치 상태 로그 기록
    if completed_in_db_count > 0:
        mismatch_message = f"미답변 탭에 표시된 리뷰 중 {completed_in_db_count}개가 DB에서는 '답변완료' 상태"
        print(f"[process_reviews_by_data_index] {mismatch_message}")
        
        # 전체 통계 저장
        save_error_log_to_supabase(
            category="시스템 통계",
            platform=platform_name,
            store_code=store_code,
            error_type="미답변탭 불일치 통계",
            error_message=mismatch_message,
            stack_trace=f"총 확인 리뷰: {len(visited_review_hashes)}, 답변완료 상태: {completed_in_db_count}, 처리된 리뷰: {processed_count}"
        )
    
    return processed_count
# 요소 유효성 확인 도우미 함수 추가
def is_element_valid(element):
    """요소가 여전히 유효한지 확인하는 도우미 함수"""
    try:
        element.tag_name  # 요소 참조 유효성 확인
        return True
    except:
        return False

###################################################
# 15) 로그아웃 함수  
###################################################
def logout_and_move_to_next(driver):
    """로그아웃하고 다음 계정으로 넘어가는 함수"""
    try:
        driver.get("https://self.baemin.com/settings")
        time.sleep(3)
        close_popups_on_homepage(driver)
        logout_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@class='LandingPage-module__mLoG']"))
        )
        logout_button.click()
        time.sleep(2)
        print("[logout_and_move_to_next] 로그아웃 완료")
    except Exception as e:
        print(f"[logout_and_move_to_next] 로그아웃 중 문제: {str(e)}")

###################################################
# 16) GUI 설정 및 메인 실행 함수
###################################################
root = tk.Tk()
root.title("배민 리뷰 자동화")

# 설정 로드
config = load_config()
driver_path = config.get('chromedriver_path', '')
driver_label = tk.Label(root, text=f"크롬드라이버 경로: {driver_path if driver_path else '미설정'}")
driver_label.pack(pady=10)

def set_driver_path():
    """크롬드라이버 경로 설정 버튼 함수"""
    global driver_path
    path = filedialog.askopenfilename(
        title="크롬드라이버 경로 선택",
        filetypes=[("ChromeDriver","*.exe"),("All files","*.*")]
    )
    if path:
        driver_label.config(text=f"크롬드라이버 경로: {path}")
        conf = load_config()
        conf['chromedriver_path'] = path
        save_config(conf)
        driver_path = path

# 버튼과 UI 요소들
btn_driver = tk.Button(root, text="크롬드라이버 경로 설정", command=set_driver_path)
btn_driver.pack(pady=5)

# 실행 모드 설정
execution_mode = tk.StringVar(value='all')
frame_mode = tk.LabelFrame(root, text="실행 범위 설정", padx=10, pady=10)
frame_mode.pack(pady=5)

rb_all = tk.Radiobutton(frame_mode, text="전체 실행", variable=execution_mode, value='all')
rb_all.grid(row=0, column=0, sticky="w")
rb_partial = tk.Radiobutton(frame_mode, text="부분 실행", variable=execution_mode, value='partial')
rb_partial.grid(row=0, column=1, sticky="w")

range_label = tk.Label(frame_mode, text="StoreCode 범위 예: AAA0010, AAA0015")
range_label.grid(row=1, column=0, columnspan=2, sticky="w")

range_entry = tk.Entry(frame_mode, width=30)
range_entry.grid(row=2, column=0, columnspan=2, pady=5, sticky="w")

###################################################
# 17) 메인 실행 함수
###################################################
def run_automation():
    """자동화 실행 버튼 클릭 시 실행되는 메인 함수"""
    global processed_reviews_in_session
    processed_reviews_in_session = set()  # 세션 초기화
    
    # 크롬드라이버 경로 확인
    if not driver_path:
        messagebox.showerror("오류", "크롬드라이버 경로를 설정해주세요.")
        return

    # 플랫폼 데이터 가져오기
    platform_data = fetch_platform_data()
    if not platform_data:
        messagebox.showerror("오류", "가져올 플랫폼 데이터가 없습니다.")
        return

    # 실행 모드에 따른 처리
    mode = execution_mode.get()
    if mode == 'partial':
        text_value = range_entry.get().strip()
        if not text_value:
            messagebox.showerror("오류", "범위를 입력하세요 (예: AAA0010, AAA0015)")
            return
        try:
            start_code, end_code = [x.strip() for x in text_value.split(',')]
            filtered = [
                r for r in platform_data
                if start_code <= r["store_code"] <= end_code
            ]
            if not filtered:
                messagebox.showinfo("정보", "해당 범위 내 매장이 없습니다.")
                return
            platform_data = filtered
            print(f"[run_automation] 부분 실행: 범위={start_code}~{end_code}, 매장수={len(platform_data)}")
        except ValueError:
            messagebox.showerror("오류", "범위 형식이 잘못되었습니다. 예: AAA0010, AAA0015")
            return
    else:
        print("[run_automation] 전체 실행 모드")

    # 인증정보별 그룹화
    creds_group = group_by_credentials(platform_data)

    # 브라우저 옵션 설정
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")

    # 드라이버 시작
    print("[run_automation] 드라이버 시작")
    driver = uc.Chrome(options=options)

    # 계정별 처리
    for (pid, ppw), rules_list in creds_group.items():
        print(f"\n[run_automation] 로그인 계정 ID={pid}, 매장수={len(rules_list)}")
        if not rules_list:
            continue

        # 로그인
        first_store_code = rules_list[0]["store_code"]
        login_success, driver = login_to_baemin(driver, pid, ppw, first_store_code, "배민", options)
        if not login_success:
            print(f"[run_automation] ID={pid} 로그인 실패 => 다음 계정으로 넘어감")
            continue

        # 매장별 처리
        for rule in rules_list:
            store_code = rule["store_code"]
            platform = rule["platform"]
            platform_code = rule["platform_code"]
            store_nm = rule["store_name"]
            print(f"[run_automation] 매장처리 시작 store_code={store_code}, platform={platform}, code={platform_code}")

            # 리뷰 페이지로 이동
            url = f"https://self.baemin.com/shops/{platform_code}/reviews"
            driver.get(url)
            time.sleep(5)
            check_and_close_new_windows(driver, store_code)  # 새창 체크

            # 팝업 처리
            close_popups_on_review_page(driver)
            close_7day_popup(driver)
            close_today_popup(driver)  # 새로 추가된 함수 호출

            # 개선된 미답변 탭 이동 함수 사용
            tab_success = navigate_to_uncommented_tab(driver, store_code)
            if not tab_success:
                print(f"[run_automation] {store_code} 미답변 탭 이동 실패, 다음 매장으로 이동")
                continue

            # 실제 리뷰 처리 (인덱스 기반 처리)
            process_reviews_by_data_index(driver, store_code, platform, platform_code, store_nm, rule)

        # 다음 계정으로 이동 전 로그아웃
        logout_and_move_to_next(driver)

    # 모든 작업 완료 후 드라이버 종료
    driver.quit()
    save_log_on_exit()  # 로그 저장
    messagebox.showinfo("완료", "배민 리뷰 자동화 처리가 완료되었습니다.")

# 실행 버튼 생성
btn_run = tk.Button(root, text="자동화 실행", command=run_automation)
btn_run.pack(pady=20)

# 메인 실행
if __name__ == "__main__":
    # 설정 파일에서 드라이버 경로 로드
    config = load_config()
    driver_path = config.get('chromedriver_path', '')
    
    # 드라이버 경로가 설정되었으면 UI에 표시
    if driver_path:
        driver_label.config(text=f"크롬드라이버 경로: {driver_path}")
    
    # 종료 시 로그 저장 설정
    root.protocol("WM_DELETE_WINDOW", lambda: [save_log_on_exit(), root.destroy()])
    
    # GUI 메인 루프 시작
    root.mainloop()