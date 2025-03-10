import os
import json
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import hashlib
import re
import unicodedata
from datetime import datetime, timedelta
import sys  # 표준 출력 리다이렉션용
import platform  # 운영체제 확인용
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

# 스크립트 최상단에 전역 변수로 추가
processed_reviews_in_session = set()

# 환경 설정
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

CONFIG_FILE = 'config_coupang.json'

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = openai

###################################################
# 1) 리뷰 해시 생성 및 ID 처리 함수들
###################################################
def generate_review_hash(store_code: str, author: str, review_text: str, order_number: str = "", 
                         review_date: str = "", star_rating: int = 0) -> str:
    """
    store_code + author(닉네임) + review_text + order_number + review_date + star_rating을 합쳐 md5 해시를 생성.
    주문번호와 날짜를 포함하여 더 고유한 식별자를 생성.
    """
    base_str = f"{store_code}_{author}_{review_text}_{order_number}_{review_date}_{star_rating}"
    hash_val = hashlib.md5(base_str.encode("utf-8")).hexdigest()
    print(f"[generate_review_hash] base_str={base_str} => hash={hash_val[:8]}...")
    return hash_val

def extract_relative_date(driver, review_element):
    """리뷰 요소에서 날짜를 추출하여 YYYY-MM-DD 형식으로 반환"""
    try:
        # 쿠팡이츠 리뷰 페이지의 날짜 형식에 맞게 수정 
        # <span class="css-1bqps6x eqn7l9b8">2025-02-25</span> 형태 추출
        date_el = review_element.find_element(By.XPATH, ".//span[contains(@class, 'css-1bqps6x')]")
        
        if date_el and date_el.text:
            date_text = date_el.text.strip()
            print(f"[extract_relative_date] 추출된 날짜: {date_text}")
            return date_text
        
        # 날짜 요소를 찾지 못한 경우 현재 날짜 반환
        today = datetime.now().date()
        return today.isoformat()
    except Exception as e:
        print(f"[extract_relative_date] 날짜 추출 중 오류: {str(e)}")
        return datetime.now().date().isoformat()

def extract_relative_date(driver, review_element):
    """리뷰 요소에서 날짜를 추출하여 YYYY-MM-DD 형식으로 반환"""
    try:
        # 쿠팡이츠 리뷰 페이지의 날짜 형식에 맞게 수정 
        # <span class="css-1bqps6x eqn7l9b8">2025-02-25</span> 형태 추출
        date_el = review_element.find_element(By.XPATH, ".//span[contains(@class, 'css-1bqps6x')]")
        
        if date_el and date_el.text:
            date_text = date_el.text.strip()
            print(f"[extract_relative_date] 추출된 날짜: {date_text}")
            return date_text
        
        # 날짜 요소를 찾지 못한 경우 현재 날짜 반환
        today = datetime.now().date()
        return today.isoformat()
    except Exception as e:
        print(f"[extract_relative_date] 날짜 추출 중 오류: {str(e)}")
        return datetime.now().date().isoformat()

def get_review_identifier(card_element, store_code):
    """카드에서 고유한 식별자 추출 - 쿠팡이츠 HTML 구조에 맞게 수정"""
    try:
        # 작성자 추출 (현재 방식이 작동함)
        author_el = card_element.find_element(By.XPATH, './/div[contains(@class,"css-hdvjju")]/b')
        author = author_el.text.strip() if author_el else "Unknown"
        
        # 리뷰 내용 추출
        try:
            review_text_el = card_element.find_element(By.XPATH, './/p[contains(@class,"css-16m6tj")]')
            review_text = review_text_el.text.strip() if review_text_el else ""
        except NoSuchElementException:
            # 대체 방법으로 시도
            review_text_els = card_element.find_elements(By.XPATH, './/td[2]//p[not(ancestor::li)]')
            review_text = ""
            for el in review_text_els:
                in_li = el.find_elements(By.XPATH, './ancestor::li')
                if not in_li:
                    review_text = el.text.strip()
                    break
        
        # 주문번호 추출 (새로 추가)
        order_number = ""
        try:
            order_number_el = card_element.find_element(
                By.XPATH, 
                './/li[strong[contains(text(),"주문번호")]]/p'
            )
            if order_number_el:
                order_text = order_number_el.text.strip()
                # 주문번호 형식이 "0RWPXFㆍ2025-02-27(주문일)" 같은 형태라면
                # 첫 부분(0RWPXF)만 추출
                if 'ㆍ' in order_text:
                    order_number = order_text.split('ㆍ')[0].strip()
                else:
                    order_number = order_text
        except NoSuchElementException:
            order_number = ""
        
        # 별점 추출
        star_rating = get_star_rating(card_element)
        
        # 날짜 추출
        try:
            date_el = card_element.find_element(By.XPATH, ".//span[contains(@class, 'css-1bqps6x')]")
            review_date = date_el.text.strip() if date_el else ""
        except:
            review_date = ""
        
        # 해시 생성 (store_code + author + review_text + order_number + rating + date)
        # 주문번호를 해시에 추가
        identifier = f"{store_code}_{author}_{review_text}_{order_number}_{star_rating}_{review_date}"
        hash_val = hashlib.md5(identifier.encode("utf-8")).hexdigest()
        
        print(f"[get_review_identifier] 식별자 생성: {hash_val[:8]}... "
              f"(작성자:{author}, 별점:{star_rating}, 주문번호:{order_number}, 날짜:{review_date})")
        return hash_val
        
    except Exception as e:
        print(f"[get_review_identifier] 식별자 생성 오류: {str(e)}")
        # DOM 속성으로 대체 식별 시도
        try:
            # 카드의 DOM 위치 정보 활용
            position = str(card_element.location)
            return hashlib.md5(f"pos_{position}".encode()).hexdigest()
        except:
            # 마지막 대안: 랜덤 ID (동일 세션에서만 사용)
            import random
            return f"random_{random.randint(10000, 99999)}"
        
def get_star_rating(row):
    """리뷰에서 별점 추출"""
    try:
        star_rating = row.parent.execute_script("""
            var el = arguments[0];
            var filledStars = el.querySelector('div.css-zjik7')
                ?.querySelectorAll('svg path[fill="#FFC400"]').length || 0;
            if(filledStars<1) filledStars=1;
            if(filledStars>5) filledStars=5;
            return filledStars;
        """, row)
        return star_rating
    except:
        return 1

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
                           .in_("platform", ["쿠팡잇츠"]) \
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
                "greeting_end": r.get("greeting_end", ""),
                "role": r.get("role", ""),
                "tone": r.get("tone", ""),
                "prohibited_words": r.get("prohibit_words", "").split(',') if r.get("prohibit_words") else [],
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
        screenshot_dir = "쿠팡_[오류]스크린샷"
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
        # 쿠팡이츠의 팝업 구조에 맞게 수정
        popup_close = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.dialog-modal-wrapper__body--close-button"))
        )
        driver.execute_script("arguments[0].click();", popup_close)
        print("[close_popups_on_homepage] 홈페이지 팝업 닫음")
    except TimeoutException:
        pass
    except Exception as e:
        print(f"[close_popups_on_homepage] 팝업 닫기 에러: {e}")
    try:
        new_popup_close = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((
                By.CSS_SELECTOR, 
                "button.dialog-modal-wrapper__body--close-button.dialog-modal-wrapper__body--close-icon--black[data-testid='Dialog__CloseButton']"
            ))
        )
        driver.execute_script("arguments[0].click();", new_popup_close)
        print("[close_popups_on_homepage] 상생 요금제 안내 팝업 닫기 성공")
        time.sleep(1)
    except TimeoutException:
        print("[close_popups_on_homepage] 상생 요금제 안내 팝업 없음")

###################################################
# 6) 안티봇 및 창 처리 관련 함수
###################################################
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

# 날짜 형식 확인 함수 (신규 추가)
def is_date_format(text):
    """
    문자열이 YYYY-MM-DD - YYYY-MM-DD 형식의 날짜 범위인지 확인
    """
    pattern = re.compile(r'\d{4}-\d{1,2}-\d{1,2}\s*-\s*\d{4}-\d{1,2}-\d{1,2}')
    return bool(pattern.search(text))

# 날짜 범위 계산 함수 (신규 추가)
def is_one_month_range(date_text):
    """날짜 범위 텍스트가 약 1개월 범위인지 확인"""
    try:
        # 날짜 추출 - "2025-1-26 - 2025-2-26" 형식
        pattern = re.compile(r'(\d{4})-(\d{1,2})-(\d{1,2})\s*-\s*(\d{4})-(\d{1,2})-(\d{1,2})')
        match = pattern.search(date_text)
        
        if match:
            # 날짜 추출
            start_year, start_month, start_day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            end_year, end_month, end_day = int(match.group(4)), int(match.group(5)), int(match.group(6))
            
            start_date = datetime(start_year, start_month, start_day)
            end_date = datetime(end_year, end_month, end_day)
            
            # 일수 차이 계산
            days_diff = (end_date - start_date).days
            
            # 25~35일 범위를 한 달로 간주 (여유 있게)
            if 25 <= days_diff <= 35:
                return True
        
        return False
    except:
        return False
    
def check_and_close_new_windows(driver, store_code=None):
    main_window = driver.current_window_handle
    curr_count = len(driver.window_handles)
    if curr_count > 1:
        store_info = f"[{store_code}] " if store_code else ""
        print(f"{store_info}[창감지] {curr_count}개 창 발견")
        handle_new_windows(driver, store_code, main_window)
    return main_window

# 페이지 상태 체크 함수
def check_page_state(driver, store_code):
    """현재 페이지가 미답변 탭인지 체크"""
    try:
        # 현재 URL 확인
        current_url = driver.current_url
        if "management/reviews" not in current_url:
            print(f"[check_page_state] 리뷰 페이지가 아님: {current_url}")
            return False
        
        # 미답변 탭이 활성화되어 있는지 확인
        try:
            is_unanswered_active = driver.execute_script("""
                var tabs = document.querySelectorAll('div.e1fz5w2d5');
                for (var tab of tabs) {
                    var label = tab.querySelector('span');
                    var isHighlighted = tab.querySelector('b.css-1k8kvzj');
                    if (label && label.textContent === '미답변' && isHighlighted) {
                        return true;
                    }
                }
                return false;
            """)
            
            if not is_unanswered_active:
                print("[check_page_state] 미답변 탭이 활성화되어 있지 않음")
                return False
        except:
            print("[check_page_state] 탭 상태 확인 실패")
            return False
        
        # 페이지 모두 정상
        return True
    except Exception as e:
        print(f"[check_page_state] 페이지 상태 확인 오류: {str(e)}")
        return False

# 페이지 설정 복구 함수 - 날짜 확인 로직 수정
def restore_page_settings(driver, store_code, store_name, platform_code):
    """페이지 설정 복구 (날짜 범위, 미답변 탭 등) - 필요한 경우에만 실행"""
    try:
        # 현재 URL 확인 - 이것만 항상 체크
        current_url = driver.current_url
        if "management/reviews" not in current_url:
            print(f"[restore_page_settings] 리뷰 페이지로 재이동 필요: {current_url}")
            # 리뷰 페이지로 다시 이동
            review_url = "https://store.coupangeats.com/merchant/management/reviews"
            driver.get(review_url)
            time.sleep(3)
            
            # 매장 선택 확인
            if not verify_store_code(driver, platform_code, store_name):
                print(f"[restore_page_settings] 매장 코드 확인 실패: {platform_code}")
                return False
                
            # 날짜 범위 설정 
            try:
                date_dropdown = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//div[input[@name='startDate']]/div"))
                )
                driver.execute_script("arguments[0].click();", date_dropdown)
                time.sleep(1)
                
                one_month_option = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//label[.//span[text()='1개월']]"))
                )
                driver.execute_script("arguments[0].click();", one_month_option)
                time.sleep(1)
                
                # 조회 버튼 클릭
                search_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(), '조회')]]"))
                )
                driver.execute_script("arguments[0].click();", search_button)
                time.sleep(2)
                
                # 미답변 탭 클릭
                if not click_unanswered_tab(driver, store_code):
                    print(f"[restore_page_settings] 미답변 탭 클릭 실패")
                    return False
            except Exception as e:
                print(f"[restore_page_settings] 설정 복구 중 오류: {str(e)}")
                return False
        
        # URL이 정상이면 아무것도 하지 않음
        return True
        
    except Exception as e:
        print(f"[restore_page_settings] 페이지 복구 중 오류: {str(e)}")
        save_error_screenshot(driver, store_code, "페이지복구실패")
        return False

###################################################
# 7) 로그인 처리 함수
###################################################
def login_to_coupang(driver, platform_id, platform_pw, store_code, platform_name, options=None):
    max_attempts = 3
    for attempt in range(1, max_attempts+1):
        try:
            print(f"[login_to_coupang] 로그인 시도 {attempt}/{max_attempts}, ID={platform_id}, store_code={store_code}")
            driver.get("https://store.coupangeats.com/merchant/login")
            time.sleep(3)

            wait = WebDriverWait(driver, 10)
            id_input = wait.until(EC.presence_of_element_located((By.ID, 'loginId')))
            pw_input = wait.until(EC.presence_of_element_located((By.ID, 'password')))
            id_input.clear()
            id_input.send_keys(platform_id)
            pw_input.clear()
            pw_input.send_keys(platform_pw)
            
            login_button = driver.find_element(By.XPATH, "//button[@type='submit']")
            login_button.click()
            print("[login_to_coupang] 로그인 버튼 클릭")
            time.sleep(5)

            # 로그인 완료 확인 (URL 변경으로)
            WebDriverWait(driver, 10).until(
                lambda d: d.current_url != "https://store.coupangeats.com/merchant/login"
            )

            error_msgs = driver.find_elements(By.CSS_SELECTOR, ".error-message")
            if error_msgs:
                for msg_el in error_msgs:
                    errtxt = msg_el.text.strip()
                    if errtxt:
                        print(f"[login_to_coupang] 로그인 에러 메시지 감지: {errtxt}")
                if attempt == max_attempts:
                    msg = f"[login_to_coupang] {store_code} 로그인 실패(최대 시도)"
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
            print(f"[login_to_coupang] {store_code} 로그인 성공")
            return True, driver

        except Exception as e:
            if attempt == max_attempts:
                msg = f"[login_to_coupang] {store_code} 로그인 중 오류: {str(e)}"
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

def validate_reply_content(ai_reply: str, max_length: int = 300, greeting_end: str = None) -> tuple:
    """
    AI 답변의 내용을 검증하고 문제가 있는 경우 거부합니다.
    
    Args:
        ai_reply: 검증할 AI 답변 텍스트
        max_length: 최대 허용 글자 수 (기본값 300자)
        greeting_end: 맺음말 텍스트 (있는 경우 포함 여부 검증)
    
    Returns:
        (is_valid: bool, reason: str)
    """
    # 기본 검증: 빈 답변 체크
    total_chars = len(ai_reply)
    if total_chars == 0:
        return False, "빈 답변"
    
    # 글자 수 검증: 최대 길이 초과 체크
    if total_chars > max_length:
        return False, f"글자 수 초과: {total_chars}자 (최대 {max_length}자)"
        
    # 맺음말 포함 여부 검증 (맺음말이 지정된 경우에만)
    if greeting_end and greeting_end.strip():
        # 하나의 맺음말만 포함되어 있는지 확인
        count = ai_reply.count(greeting_end)
        if count == 0:
            actual_ending = ai_reply[-min(len(greeting_end)*2, len(ai_reply)):]
            return False, f"맺음말 '{greeting_end}' 누락 - 현재 맺음말: '{actual_ending}'"
        elif count > 1:
            return False, f"맺음말 '{greeting_end}' {count}번 중복 포함됨"
    
    # 한글, 기본 문장 부호, 숫자만 허용
    valid_pattern = re.compile(r'[가-힣\s.,!?0-9%]')
    valid_chars = len([c for c in ai_reply if valid_pattern.match(c)])

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

        store_code = store_info.get('store_code', 'unknown')
        print(f"[generate_ai_reply] [{store_code}] 리뷰 텍스트: {review_text[:30]}...")
        is_empty_review = not review_text or review_text.strip() == ""

        store_type = store_info.get('store_type', 'delivery_only')
        greeting_start = store_info.get('greeting_start', '안녕하세요')
        greeting_end   = store_info.get('greeting_end', '')  # 기본값을 빈 문자열로 설정
        max_length     = store_info.get('max_length', 300)
        rating         = store_info.get('rating', 0)
        author         = store_info.get('author', '고객')

        # 중요 파라미터 검증
        if not greeting_start:
            print(f"[generate_ai_reply] [{store_code}] 경고: 인사말이 설정되지 않음")
            greeting_start = '안녕하세요'
            
        # 맺음말이 null이면 경고만 출력하고 빈 문자열로 설정
        if not greeting_end:
            print(f"[generate_ai_reply] [{store_code}] 맺음말이 설정되지 않음 - 자연스러운 맺음말 사용")
            greeting_end = ''

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

        # Continue with the rest of the prompt, 맺음말 관련 지시는 greeting_end가 있을 때만 추가
        system_prompt_parts.extend([
            "",
            "## 답변 형식",
            f"- 길이: {max_length}자 내외",
            "- 문체: 정중하고 친근한 한국어 (반말 사용 금지)",
            "- 호칭: 이름 뒤 '님' 필수",
            f"- 시작: '{greeting_start}'",
        ])
        
        # greeting_end가 있는 경우에만 맺음말 지시사항 추가
        if greeting_end:
            system_prompt_parts.extend([
                f"- 종료: '{greeting_end}'",
                "",
                "## 중요: 답변 형식 준수",
                "1. 반드시 답변 마지막에 한 번만 맺음말을 넣으세요.",
                f"2. 맺음말 형식: '{greeting_end}' (정확히 이 형식으로)",
                "3. 맺음말 앞뒤로 추가 문구나 공백을 넣지 마세요.",
                "4. 맺음말이 여러 번 중복되지 않도록 주의하세요."
            ])
        else:
            system_prompt_parts.extend([
                "",
                "## 답변 종료",
                "1. 자연스럽고 정중한 맺음말로 답변을 마무리하세요.",
                "2. 예: '다시 찾아주시면 더 좋은 서비스로 보답하겠습니다', '더 나은 서비스로 보답하겠습니다' 등",
                "3. 맺음말은 정형화된 형식이 아닌 리뷰 내용에 맞게 자연스럽게 작성하세요."
            ])
        
        # 공통 부분 이어서 추가
        system_prompt_parts.extend([
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
            "- 부정적 단어 사용",
        ])
        
        # greeting_end가 있는 경우에만 맺음말 지시 추가
        if greeting_end:
            system_prompt_parts.extend([
                "",
                "## 중요: 반드시 다음 맺음말로 답변을 끝내세요",
                f"반드시 '{greeting_end}'(으)로 답변을 끝내세요. 다른 맺음말 사용 금지."
            ])

        # Join all parts with newlines
        system_prompt = "\n".join(system_prompt_parts)

        # 사용자 프롬프트도 맺음말 지시를 조건부로 적용
        user_prompt = f"""리뷰 작성자 이름과 별점, 리뷰 내용을 보고 답변을 작성해주세요:
작성자: {author}
별점: {rating}점
리뷰: {review_text if not is_empty_review else '(리뷰 내용 없음)'}"""

        # greeting_end가 있는 경우에만 맺음말 지시 추가
        if greeting_end:
            user_prompt += f"\n\n중요: 반드시 '{greeting_end}'(으)로 답변을 끝내세요."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            print(f"[generate_ai_reply] [{store_code}] OpenAI API 요청 시작")
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=600
            )
            ai_reply = response.choices[0].message.content
            ai_reply = clean_ai_reply(ai_reply)
            print(f"[generate_ai_reply] [{store_code}] 생성된 답글 (길이: {len(ai_reply)}자): {ai_reply[:40]}...")
            
            # 맺음말 포함 확인 및 추가 로직 수정 - greeting_end가 있는 경우만 적용
            if greeting_end and not ai_reply.endswith(greeting_end):
                print(f"[generate_ai_reply] [{store_code}] 경고: 생성된 답글에 맺음말 '{greeting_end}' 누락됨")
                # 맺음말 없으면 자동 추가 (한 번 더 시도)
                if len(ai_reply) + len(greeting_end) + 3 <= max_length:
                    # 마지막 문장 끝 확인
                    last_sentence_end = max(ai_reply.rfind('.'), ai_reply.rfind('!'), ai_reply.rfind('?'))
                    
                    if last_sentence_end > 0 and last_sentence_end > len(ai_reply) - 10:
                        # 문장 끝에 맺음말 추가
                        ai_reply = ai_reply[:last_sentence_end] + f" {greeting_end}."
                    else:
                        # 문장 끝이 없으면 그냥 추가
                        if ai_reply.endswith('.') or ai_reply.endswith('!') or ai_reply.endswith('?'):
                            ai_reply = ai_reply[:-1] + f" {greeting_end}."
                        else:
                            ai_reply = ai_reply + f" {greeting_end}."
                    
                    print(f"[generate_ai_reply] [{store_code}] 맺음말 자동 추가됨: {ai_reply[-40:]}")
                
            return ai_reply
            
        except Exception as api_error:
            print(f"[generate_ai_reply] [{store_code}] OpenAI API 호출 오류: {str(api_error)}")
            # API 오류 자세한 내용 기록
            if hasattr(api_error, 'response'):
                try:
                    error_data = api_error.response
                    print(f"[generate_ai_reply] [{store_code}] API 오류 상세: {error_data}")
                except:
                    pass
            return None

    except Exception as e:
        print(f"[generate_ai_reply] AI 답변 생성 오류: {e}")
        return None

def generate_ai_reply_with_retry(review_text: str, store_info: dict, max_attempts: int = 3) -> str:
    """
    품질 검증을 통과할 때까지 최대 3회까지 재시도하는 답변 생성 함수
    - greeting_end가 있는 경우: 맺음말이 항상 포함되도록 AI에게 요청 길이를 더 짧게 설정
    - greeting_end가 없는 경우: 자연스러운 맺음말 허용
    """
    
    best_reply = None
    best_score = 0
    failure_reasons = []  # 실패 이유를 저장할 리스트
    
    # 필요한 정보 추출
    max_length = store_info.get('max_length', 300)
    greeting_start = store_info.get('greeting_start', '안녕하세요')
    greeting_end = store_info.get('greeting_end', '')  # 기본값 빈 문자열
    store_code = store_info.get('store_code', 'unknown')
    
    # greeting_end 존재 여부에 따른 처리
    has_greeting_end = greeting_end and greeting_end.strip()
    
    # 실제 사용 가능한 최대 길이 계산 (맺음말 보장)
    if has_greeting_end:
        greeting_end_length = len(greeting_end)
        greeting_start_length = len(greeting_start) if greeting_start else 0
        
        # AI에게 요청할 최대 길이는 최종 길이보다 20자 이상 짧게 설정
        # (맺음말 길이 + 여유 공간)
        effective_max_length = max_length - greeting_end_length - 10
        
        print(f"[generate_ai_reply_with_retry] 글자수 설정: 최대={max_length}, AI 요청={effective_max_length}, "
              f"맺음말 길이={greeting_end_length}, 시작글 길이={greeting_start_length}")
    else:
        # 맺음말이 없는 경우, 전체 길이 사용
        effective_max_length = max_length - 10  # 약간의 여유 공간
        print(f"[generate_ai_reply_with_retry] 맺음말 없음: 최대 길이={max_length}, AI 요청={effective_max_length}")
    
    # 시스템 프롬프트에 명확한 지시 추가
    updated_store_info = store_info.copy()
    updated_store_info['max_length'] = effective_max_length
    
    for attempt in range(max_attempts):
        # AI 답변 생성
        ai_reply = generate_ai_reply(review_text, updated_store_info)
        if not ai_reply:
            failure_reason = f"Attempt {attempt + 1}: AI 답변 생성 실패 (generate_ai_reply에서 None 반환)"
            print(f"[generate_ai_reply_with_retry] {failure_reason}")
            failure_reasons.append(failure_reason)
            continue
            
        print(f"[generate_ai_reply_with_retry] Attempt {attempt + 1}: 생성된 답변 길이: {len(ai_reply)}자")
        
        # greeting_end가 존재하는 경우에만 맺음말 확인 및 추가 로직 적용
        if has_greeting_end and not ai_reply.endswith(greeting_end):
            print(f"[generate_ai_reply_with_retry] Attempt {attempt + 1}: 맺음말 추가 필요 - 현재 맺음말: '{ai_reply[-20:]}'")
            
            # 맺음말 중복 체크
            if greeting_end in ai_reply:
                # 이미 포함되어 있지만 끝에 없는 경우, 중복을 제거하고 끝에 추가
                ai_reply = ai_reply.replace(greeting_end, "", ai_reply.count(greeting_end) - 1)
                
                # 마지막 greeting_end가 문장 끝이 아니면 조정
                if not ai_reply.endswith(greeting_end):
                    last_idx = ai_reply.rfind(greeting_end)
                    if last_idx > 0:
                        before_part = ai_reply[:last_idx].rstrip()
                        ai_reply = f"{before_part} {greeting_end}."
            else:
                # 포함되어 있지 않은 경우 추가
                last_sentence_end = max(ai_reply.rfind('.'), ai_reply.rfind('!'), ai_reply.rfind('?'))
                
                if last_sentence_end > 0 and last_sentence_end > len(ai_reply) - 10:
                    ai_reply = ai_reply[:last_sentence_end] + f" {greeting_end}."
                else:
                    if ai_reply.endswith('.') or ai_reply.endswith('!') or ai_reply.endswith('?'):
                        ai_reply = ai_reply[:-1] + f" {greeting_end}."
                    else:
                        ai_reply = ai_reply + f" {greeting_end}."
        
        # 최종 길이 검사
        if len(ai_reply) > max_length:
            print(f"[generate_ai_reply_with_retry] Attempt {attempt + 1}: 길이 초과: {len(ai_reply)}자 > {max_length}자")
            
            # 맺음말 유지하면서 내용 자르기
            if has_greeting_end:
                content_to_keep = max_length - len(greeting_end) - 3  # 공백과 구두점 고려
                
                # 맺음말 제외한 내용 추출
                content_without_ending = ai_reply
                if ai_reply.endswith(greeting_end):
                    content_without_ending = ai_reply[:-len(greeting_end)-1].strip()
                
                # 내용 자르기 (최대한 어절 단위로)
                truncated_content = content_without_ending[:content_to_keep].strip()
                
                # 마지막 어절이 잘렸는지 확인하고 필요시 더 자르기
                last_space_pos = truncated_content.rfind(' ')
                if last_space_pos > content_to_keep * 0.7:  # 내용의 70% 이상 위치에 공백이 있으면
                    truncated_content = truncated_content[:last_space_pos]
                
                # 자른 내용에 맺음말 다시 추가
                ai_reply = f"{truncated_content} {greeting_end}."
            else:
                # 맺음말이 없는 경우, 문장 단위로 자르기
                content_to_keep = max_length - 3  # 공백과 구두점 고려
                truncated_content = ai_reply[:content_to_keep].strip()
                
                # 마지막 문장이 잘렸는지 확인
                last_sentence_end = max(
                    truncated_content.rfind('.'), 
                    truncated_content.rfind('!'), 
                    truncated_content.rfind('?')
                )
                if last_sentence_end > content_to_keep * 0.7:  # 내용의 70% 이상 위치에 문장 끝이 있으면
                    truncated_content = truncated_content[:last_sentence_end+1]
                else:
                    # 문장 끝을 찾지 못하면 어절 단위로 자르기
                    last_space_pos = truncated_content.rfind(' ')
                    if last_space_pos > content_to_keep * 0.7:
                        truncated_content = truncated_content[:last_space_pos]
                
                ai_reply = truncated_content.strip()
                # 마침표로 끝나지 않으면 추가
                if not (ai_reply.endswith('.') or ai_reply.endswith('!') or ai_reply.endswith('?')):
                    ai_reply += "."
                
            print(f"[generate_ai_reply_with_retry] Attempt {attempt + 1}: 길이 조정 후: {len(ai_reply)}자")
        
        # 품질 검증
        is_valid, reason = validate_reply_content(ai_reply, max_length, greeting_end if has_greeting_end else None)
        if not is_valid:
            failure_reason = f"Attempt {attempt + 1}: 검증 실패: {reason}"
            print(f"[generate_ai_reply_with_retry] {failure_reason}")
            failure_reasons.append(failure_reason)

            # 다음 시도에서는 더 짧게 요청
            if "글자 수 초과" in reason:
                new_length = int(effective_max_length * 0.85)  # 15% 더 줄임
                updated_store_info['max_length'] = new_length
                print(f"[generate_ai_reply_with_retry] Attempt {attempt + 1}: 다음 시도는 더 짧게 요청: {new_length}자")
            
            continue

        # 품질 점수 평가
        try:
            is_good, scores = score_reply(ai_reply, review_text, 
                            store_info.get('author', ''), 
                            store_info.get('rating', 0))

            # 점수 추출
            score = sum(scores.values()) if isinstance(scores, dict) else 70
            print(f"[generate_ai_reply_with_retry] Attempt {attempt + 1}: 품질 점수: {score}")
            
            # 더 나은 답변 저장
            if score > best_score:
                best_score = score
                best_reply = ai_reply
                
            # 목표 점수 달성시 즉시 반환
            if is_good:
                print(f"[generate_ai_reply_with_retry] Attempt {attempt + 1}: 최종 답변 완료 - 길이: {len(ai_reply)}자")
                return ai_reply
            else:
                failure_reason = f"Attempt {attempt + 1}: 품질 점수 부족 - 점수: {score}, 필요 점수: 80+"
                print(f"[generate_ai_reply_with_retry] {failure_reason}")
                failure_reasons.append(failure_reason)
                
        except Exception as e:
            failure_reason = f"Attempt {attempt + 1}: 품질 평가 중 오류 - {str(e)}"
            print(f"[generate_ai_reply_with_retry] {failure_reason}")
            failure_reasons.append(failure_reason)
            
            # 오류 발생해도 답변 저장
            if ai_reply and (best_reply is None or len(ai_reply) > 0):
                best_reply = ai_reply
                best_score = 70  # 기본 점수 부여
    
    # 모든 시도 후에도 충분한 점수를 얻지 못하면 최고 점수 답변 반환
    if best_reply:
        print(f"[generate_ai_reply_with_retry] 최종 답변 생성 (최고 점수: {best_score}) - 길이: {len(best_reply)}자")
        return best_reply
        
    # 디버깅을 위한 상세 정보 로깅
    print(f"[CRITICAL ERROR] {store_code} - 유효한 답변 생성 실패 - 상세 원인:")
    for idx, reason in enumerate(failure_reasons):
        print(f"  {idx+1}. {reason}")
    
    # 맺음말 설정이 잘못되었을 가능성도 체크
    if has_greeting_end:
        print(f"[DEBUG] 맺음말 설정: '{greeting_end}'")
    else:
        print(f"[DEBUG] 맺음말이 설정되지 않음 - 자연스러운 맺음말 사용")
    
    # 리뷰 텍스트 길이 및 내용 일부 로깅
    review_preview = review_text[:50] + "..." if len(review_text) > 50 else review_text
    print(f"[DEBUG] 리뷰 텍스트 (길이: {len(review_text)}): {review_preview}")
    
    raise Exception(f"유효한 답변 생성 실패 - 시도 {max_attempts}회 모두 실패: {', '.join(failure_reasons)}")

def generate_prohibited_free_reply(review_text, store_info):
    """금지어를 피해 AI 답변 생성"""
    try:
        if not openai.api_key:
            print("[generate_prohibited_free_reply] OpenAI API 키가 설정되지 않음.")
            return None

        store_code = store_info.get('store_code', 'unknown')
        greeting_start = store_info.get('greeting_start', '안녕하세요')
        greeting_end = store_info.get('greeting_end', '')  # 기본값 빈 문자열
        max_length = store_info.get('max_length', 300)
        rating = store_info.get('rating', 0)
        author = store_info.get('author', '고객님')  # 기본값으로 '고객님' 사용
        prohibited_words = store_info.get('prohibited_words', [])
        store_type = store_info.get('store_type', 'delivery_only')
        detected_word = store_info.get('detected_prohibited_word')
        
        # 맺음말 존재 여부 확인
        has_greeting_end = greeting_end and greeting_end.strip()
        
        # 감지된 금지어가 있으면 prioritized_prohibited_words에 추가
        prioritized_prohibited_words = prohibited_words.copy()
        if detected_word and detected_word not in prioritized_prohibited_words:
            prioritized_prohibited_words.append(detected_word)
            print(f"[generate_prohibited_free_reply] 팝업에서 감지된 금지어 '{detected_word}' 추가")
        
        # 리뷰 내용에서 금지어가 있는지 확인하고 마스킹된 버전 생성
        masked_review = review_text
        detected_words_in_review = []
        
        for word in prioritized_prohibited_words:
            if word and word in review_text:
                masked_review = masked_review.replace(word, "****")
                detected_words_in_review.append(word)
                print(f"[generate_prohibited_free_reply] 리뷰에서 금지어 '{word}' 발견 및 마스킹")
        
        if detected_words_in_review:
            print(f"[generate_prohibited_free_reply] 리뷰에서 발견된 금지어: {', '.join(detected_words_in_review)}")
        
        # 금지어 목록을 문자열로 변환
        prohibited_words_str = ", ".join(prioritized_prohibited_words) if prioritized_prohibited_words else "없음"
        
        # 특별 경고 생성
        special_warning = ""
        if detected_word or detected_words_in_review:
            special_warning = "\n## 특별 주의사항\n"
            if detected_word:
                special_warning += f"이전 답변에서 다음 금지어가 감지되었습니다: '{detected_word}'\n"
            if detected_words_in_review:
                special_warning += f"리뷰에 다음 금지어가 포함되어 있습니다: {', '.join(detected_words_in_review)}\n"
            special_warning += "이 단어들을 절대 포함하지 말고, 리뷰 내용을 직접 인용하지 마세요. 대신 순화된 표현을 사용하세요.\n"
            special_warning += "예시: '맛있는 음식' (O), '좋은 메뉴' (O) - 원문 그대로 인용 금지\n"
        
        system_prompt = f"""
당신은 {store_type} 유형의 가게 사장님입니다.

## 중요: 금지어 피하기
다음 단어들은 금지어이므로 절대 사용하지 마세요: {prohibited_words_str}
또한 고객의 이름을 직접 언급하지 말고, 대신 '고객님'이라고 호칭하세요.{special_warning}

## 절대적 규칙
1. 리뷰 내용을 직접 인용하지 마세요. 특히 금지어가 포함된 구절은 절대 인용하지 마세요.
2. 금지어가 포함된 표현은 일반적인 표현으로 대체하세요. (예: "맛있는 음식에 감사드립니다")
3. 고객 리뷰의 의도만 파악하고, 다른 말로 순화하여 표현하세요.

## 기본 응대 원칙
1. 고객의 구체적인 피드백 포인트를 언급하되, 금지어가 없는 방식으로 표현하세요.
2. 부정적 리뷰에도 변명하지 않고 개선 의지를 진정성 있게 전달하세요.
3. 긍정적 피드백은 구체적으로 감사를 표현하고 더 나은 서비스를 약속하세요.

## 답변 형식
- 길이: {max_length}자 내외
- 문체: 정중하고 친근한 한국어
- 시작: '{greeting_start}'"""

        # 맺음말 지시 추가 (맺음말이 있는 경우만)
        if has_greeting_end:
            system_prompt += f"""
- 종료: '{greeting_end}'

## 중요: 맺음말 형식
반드시 '{greeting_end}'(으)로 답변을 끝내세요."""
        else:
            system_prompt += """

## 답변 종료
자연스럽고 정중한 맺음말로 답변을 마무리하세요. 정형화된 형식이 아닌 리뷰 내용에 맞게 자연스럽게 작성하세요."""

        # 사용자 프롬프트도 맺음말 지시를 조건부로 적용
        user_prompt = f"""리뷰 정보:
별점: {rating}점
리뷰 내용(금지어 포함): {masked_review if masked_review else '(리뷰 내용 없음)'}

위 리뷰에 대한 답변을 작성해주세요.
중요: 
1. 리뷰 내용을 직접 인용하지 마세요!
2. 금지어({prohibited_words_str})를 절대 사용하지 마세요!"""

        # 맺음말 지시 추가 (맺음말이 있는 경우만)
        if has_greeting_end:
            user_prompt += f"\n3. 반드시 '{greeting_end}'(으)로 답변을 끝내세요!"

        # 개선된 AI 답변 생성 요청
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=600
        )
        
        ai_reply = response.choices[0].message.content
        ai_reply = clean_ai_reply(ai_reply)
        
        # 생성된 답변에서 금지어 추가 확인 및 제거
        for word in prioritized_prohibited_words:
            if word and word in ai_reply:
                print(f"[generate_prohibited_free_reply] 생성된 답변에서 금지어 '{word}' 발견 및 대체")
                ai_reply = ai_reply.replace(word, "****")
        
        # 맺음말 확인 및 필요시 추가 - 맺음말이 있는 경우에만 적용
        if has_greeting_end and not ai_reply.endswith(greeting_end):
            if greeting_end in ai_reply:
                # 맺음말이 중간에 있으면 마지막에만 유지
                last_idx = ai_reply.rfind(greeting_end)
                before_part = ai_reply[:last_idx].rstrip()
                ai_reply = f"{before_part} {greeting_end}."
            else:
                # 맺음말 없으면 추가
                last_sentence_end = max(ai_reply.rfind('.'), ai_reply.rfind('!'), ai_reply.rfind('?'))
                if last_sentence_end > 0 and last_sentence_end > len(ai_reply) - 10:
                    ai_reply = ai_reply[:last_sentence_end] + f" {greeting_end}."
                else:
                    if ai_reply.endswith('.') or ai_reply.endswith('!') or ai_reply.endswith('?'):
                        ai_reply = ai_reply[:-1] + f" {greeting_end}."
                    else:
                        ai_reply = ai_reply + f" {greeting_end}."
        
        print(f"[generate_prohibited_free_reply] 금지어 없는 답변 생성 완료: {ai_reply[:40]}...")
        return ai_reply
    
    except Exception as e:
        print(f"[generate_prohibited_free_reply] 금지어 없는 답변 생성 오류: {str(e)}")
        return None

###################################################
# 10) AI 답변 품질 평가 함수
###################################################
def score_reply(ai_reply: str, original_review: str, original_author: str = "", original_rating: int = 0, threshold: int = 80) -> tuple:
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

def _check_duplicate_review(driver, store_code, platform_name, review_hash, author, review_text, review_date):
    """
    중복 리뷰 체크 및 날짜 기반 처리 결정:
    1. 리뷰가 DB에 존재하는지 확인
    2. 상태별 다른 처리:
       - "답변완료": 다시 처리하지 않음 (단, 미답변 탭에 있는 경우 재처리)
       - "사장님 확인필요": 2일 이상 경과한 경우만 재처리
       - "답변대기": 1일 이상 경과한 경우만 재처리
       - 그 외: 날짜 체크
    
    Returns:
        (bool, int, str): (스킵 여부, 현재 재시도 횟수, 상태) - True면 스킵, False면 계속 처리
    """
    try:
        today_date = datetime.now().date()
        
        # 리뷰 날짜가 문자열 형태인 경우 datetime 객체로 변환
        if isinstance(review_date, str):
            try:
                review_date_obj = datetime.strptime(review_date, "%Y-%m-%d").date()
            except ValueError:
                # 날짜 형식이 맞지 않으면 오늘 날짜 사용
                review_date_obj = today_date
        else:
            review_date_obj = today_date
            
        # 날짜 차이 계산 (오늘 - 리뷰날짜)
        days_passed = (today_date - review_date_obj).days
        
        # DB에서 기존 리뷰 조회
        existing = supabase.table("reviews").select("*").eq("review_id", review_hash).execute()
        
        if not existing.data:
            # 기존 리뷰 없음 - 날짜 체크: 하루가 지났는지 확인
            if days_passed < 1:
                print(f"[_check_duplicate_review] 새 리뷰 발견: {review_hash[:8]} - 작성된지 1일 미만이라 답변대기 상태로 저장")
                return True, 0, "답변대기"  # 1일 미만은 스킵하고 답변대기 상태로 저장
            return False, 0, ""  # 1일 이상은 처리
            
        record = existing.data[0]
        status = record.get('response_status', '')
        retry_count = record.get('retry_count', 0)
        
        if status == "답변완료":
            # 미답변 탭에서는 "답변완료" 상태가 오류이므로 다시 처리
            print(f"[_check_duplicate_review] 답변완료 상태인데 미답변 탭에 있는 리뷰 발견: {review_hash[:8]} - 재처리")
            save_error_log_to_supabase(
                category="데이터 불일치",
                platform=platform_name,
                store_code=store_code,
                error_type="답변완료 상태 불일치",
                error_message=f"DB에는 답변완료지만 미답변 탭에 표시됨: {author} / {review_text[:20]}..."
            )
            return False, retry_count, status  # 재처리하도록 False 반환
            
        elif status == "사장님 확인필요":
            if days_passed < 2:  # 2일 미만 대기
                print(f"[_check_duplicate_review] 사장님 확인필요 리뷰 스킵 (경과일: {days_passed}일, 2일 이후 처리 예정)")
                return True, retry_count, status  # 2일 미만은 스킵
            else:
                print(f"[_check_duplicate_review] 사장님 확인필요 리뷰 처리 시작 (경과일: {days_passed}일, 2일 이상 경과)")
                return False, retry_count, status  # 2일 이상은 재처리
        
        elif status == "답변대기":
            if days_passed < 1:  # 1일 미만 대기
                print(f"[_check_duplicate_review] 답변대기 리뷰 스킵 (경과일: {days_passed}일, 1일 이후 처리 예정)")
                return True, retry_count, status  # 1일 미만은 스킵
            else:
                print(f"[_check_duplicate_review] 답변대기 리뷰 처리 시작 (경과일: {days_passed}일, 1일 이상 경과)")
                return False, retry_count, status  # 1일 이상은 재처리
        
        # 실패, 미답변 등 다른 상태는 모두 즉시 처리
        print(f"[_check_duplicate_review] 미완료 리뷰({status}) 즉시 처리: {review_hash[:8]}")
        return False, retry_count, status
            
    except Exception as e:
        # 조회 오류 시 안전하게 처리 진행
        print(f"[_check_duplicate_review] 중복 체크 중 오류: {str(e)}")
        return False, 0, ""

###################################################
# 12) 댓글 등록 처리 함수
###################################################

def sanitize_reply(reply_text, greeting_end=None):
    """답변 내용 정리 및 맺음말 중복 제거"""
    # 정규화
    clean_text = unicodedata.normalize('NFC', reply_text)
    
    # 맺음말이 있는 경우 중복 처리
    if greeting_end and greeting_end.strip():
        # 맺음말 출현 횟수 확인
        count = clean_text.count(greeting_end)
        if count > 1:
            # 첫 번째 맺음말 이전 부분만 추출
            first_idx = clean_text.find(greeting_end)
            if first_idx > 0:
                clean_text = clean_text[:first_idx].strip() + " " + greeting_end
    
    return clean_text

def click_and_submit_comment(driver, card_element, ai_reply_text, author_name, max_attempts=3):
    """댓글 등록 로직 - 맺음말 중복 제거 및 API 오류 처리"""
    for attempt in range(1, max_attempts + 1):
        try:
            # 댓글 영역으로 스크롤
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                card_element
            )
            time.sleep(2)

            # 댓글 등록 버튼 찾기
            comment_btn = WebDriverWait(card_element, 5).until(
                EC.element_to_be_clickable((
                    By.XPATH, 
                    ".//button[contains(text(),'사장님 댓글 등록하기')]"
                ))
            )
            driver.execute_script("arguments[0].click();", comment_btn)
            time.sleep(2)

            # 텍스트 영역 찾기
            text_area = WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located((By.XPATH, "//textarea[@name='review']"))
            )

            # 맺음말 검사 및 중복 제거 - 핵심 수정 부분
            greeting_end = "덕분에 웃을수있고 먹고 살고 있습니다\n\n행복하세요"
            clean_reply = sanitize_reply(ai_reply_text, greeting_end)
            
            # 텍스트 길이 출력
            print(f"[click_and_submit_comment] 원본 길이: {len(ai_reply_text)}, 정리 후 길이: {len(clean_reply)}")
            
            # 직접 값 설정
            text_area.clear()
            driver.execute_script("arguments[0].value = arguments[1];", text_area, clean_reply)
            time.sleep(2)
            
            # 실제 입력값 확인
            current_val = text_area.get_attribute('value')
            print(f"[click_and_submit_comment] 입력된 텍스트 길이: {len(current_val)} 글자")
            
            # "등록" 버튼 찾고 클릭
            submit_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(),'등록')]]"))
            )
            driver.execute_script("arguments[0].click();", submit_btn)
            time.sleep(2)

            # API 오류 팝업 처리
            try:
                popup = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(text(), '외부 API 호출')]"))
                )
                print("[click_and_submit_comment] API 오류 팝업 발견")
                ok_btn = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(), '확인')]]"))
                )
                driver.execute_script("arguments[0].click();", ok_btn)
                time.sleep(2)
                
                # 취소 버튼 클릭하여 댓글 모달 닫기
                try:
                    cancel_btn = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(),'취소')]]"))
                    )
                    driver.execute_script("arguments[0].click();", cancel_btn)
                    time.sleep(2)
                except:
                    pass
                
                return "API_ERROR", None
                
            except TimeoutException:
                # 금지어 팝업 확인 (기존 로직)
                try:
                    popup_elem = WebDriverWait(driver, 5).until(
                        EC.visibility_of_element_located((
                            By.XPATH, 
                            "//div[contains(@class, 'dialog-modal-wrapper__body')]"
                        ))
                    )
                    print("[click_and_submit_comment] 금지어 팝업 발견")
                    
                    # 팝업 텍스트 확인
                    popup_text = popup_elem.text
                    print(f"[click_and_submit_comment] 금지어 팝업 내용: {popup_text}")
                    
                    # 금지어 추출 시도
                    detected_word = None
                    import re
                    match = re.search(r"'([^']+)'", popup_text)
                    if match:
                        detected_word = match.group(1)
                        print(f"[click_and_submit_comment] 팝업에서 감지된 금지어: '{detected_word}'")
                    
                    # 작성자 이름 관련 금지어인지 확인
                    is_name_prohibited = False
                    if detected_word == author_name or "이름" in popup_text or "작성자" in popup_text or author_name in popup_text:
                        is_name_prohibited = True
                        print(f"[click_and_submit_comment] 작성자 이름 관련 금지어로 판단됨")
                    
                    # 팝업이 발견되면 '확인' 버튼 클릭
                    ok_btn = WebDriverWait(popup_elem, 3).until(
                        EC.element_to_be_clickable((
                            By.XPATH,
                            ".//button[contains(@class, 'button--primaryContained')]" +
                            "/span[contains(text(), '확인')]/.."
                        ))
                    )
                    driver.execute_script("arguments[0].click();", ok_btn)
                    time.sleep(1)
                    
                    # 취소 버튼 클릭해서 모달 닫기
                    try:
                        cancel_btn = WebDriverWait(driver, 3).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(),'취소')]]"))
                        )
                        driver.execute_script("arguments[0].click();", cancel_btn)
                        time.sleep(1)
                    except:
                        pass
                    
                    # 유형별 금지어 결과 반환
                    if is_name_prohibited:
                        return "PROHIBITED_NAME", author_name
                    else:
                        return "PROHIBITED_CONTENT", detected_word
                        
                except TimeoutException:
                    # 팝업이 없으면 성공으로 간주
                    print(f"[click_and_submit_comment] 댓글 등록 성공 (시도 {attempt}/{max_attempts})")
                    return True, None

        except Exception as e:
            print(f"[click_and_submit_comment] 댓글 등록 시도 {attempt} 실패: {e}")
            time.sleep(2)
            
            # 모달 닫기 시도
            try:
                cancel_btn = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(),'취소')]]"))
                )
                driver.execute_script("arguments[0].click();", cancel_btn)
                time.sleep(1)
            except:
                pass

    # 모든 시도 실패
    return False, None

def verify_comment_submission(driver, card_element, review_hash, store_code):
    """
    댓글 등록 성공 여부를 확인하는 함수
    - 등록 버튼 존재 여부로 확인
    - 버튼이 없으면 성공, 있으면 실패
    
    Args:
        driver: WebDriver 인스턴스
        card_element: 리뷰 카드 요소
        review_hash: 리뷰 식별자
        store_code: 매장 코드
        
    Returns:
        True: 댓글 등록 성공
        False: 댓글 등록 실패
        None: 확인 불가
    """
    try:
        # 페이지 새로고침 (변경사항 반영을 위해)
        driver.refresh()
        time.sleep(3)
        
        # 현재 페이지에서 모든 리뷰 카드 가져오기
        all_cards = driver.find_elements(By.XPATH, '//tr')
        
        # 리뷰 ID로 해당 카드 찾기
        for card in all_cards:
            current_hash = get_review_identifier(card, store_code)
            
            if current_hash == review_hash:
                # 사장님 댓글 등록하기 버튼 존재 여부 확인
                comment_btns = card.find_elements(By.XPATH, ".//button[contains(text(),'사장님 댓글 등록하기')]")
                
                if comment_btns:
                    print(f"[verify_comment_submission] 댓글 등록 실패 확인: 버튼이 여전히 존재함")
                    return False
                else:
                    print(f"[verify_comment_submission] 댓글 등록 성공 확인: 버튼이 없음")
                    return True
                
        # 리뷰를 찾지 못한 경우
        print(f"[verify_comment_submission] 리뷰 카드를 찾지 못함: {review_hash[:8]}")
        return None
        
    except Exception as e:
        print(f"[verify_comment_submission] 확인 중 오류: {str(e)}")
        return None
###################################################
# 13) 리뷰 카드 처리 핵심 함수
###################################################
def handle_review_card(
    driver, 
    store_code, 
    platform_name, 
    platform_code,
    store_nm,
    card_element,
    rule
):
    """
    리뷰 카드 처리 함수 - 쿠팡이츠 버전 (개선됨)
    - 맺음말 제한이 적용된 함수 사용
    - 매칭 오류 문제 해결을 위해 작성자와 리뷰 미리보기 추가 검증
    """
    # 1. 답글 버튼 체크
    try:
        comment_btn = card_element.find_elements(By.XPATH, ".//button[contains(text(),'사장님 댓글 등록하기')]")
        if not comment_btn:
            print("[handle_review_card] 답글 불가능한 리뷰(버튼 없음)")
            return None
    except Exception as e:
        print(f"[handle_review_card] 답글 버튼 찾기 실패: {str(e)}")
        return None

    # 2. 리뷰 정보 파싱
    try:
        # 작성자
        author_el = card_element.find_element(By.XPATH, './/div[contains(@class,"css-hdvjju")]/b')
        author = author_el.text.strip() if author_el else "Unknown"
    except Exception:
        author = "(Unknown)"

    # 별점
    star_rating = get_star_rating(card_element)
    
    # 리뷰 내용
    try:
        review_text_el = card_element.find_element(By.XPATH, './/p[contains(@class,"css-16m6tj")]')
        review_text = review_text_el.text.strip() if review_text_el else ""
    except Exception:
        try:
            review_text_els = card_element.find_elements(By.XPATH, './/td[2]//p[not(ancestor::ul)]')
            review_text = ""
            for el in review_text_els:
                in_li = el.find_elements(By.XPATH, './ancestor::li')
                if not in_li:
                    review_text = el.text.strip()
                    break
        except Exception:
            review_text = ""

    # 주문메뉴 (쿠팡이츠 구조에 맞게 수정)
    try:
        order_menu_els = card_element.find_elements(
            By.XPATH, 
            './/li[strong[contains(text(),"주문 메뉴")]]/span'
        )
        order_menu = ", ".join([el.text.strip() for el in order_menu_els if el.text.strip()]) if order_menu_els else ""
    except Exception:
        order_menu = ""

    # 배달리뷰 (쿠팡이츠는 별도의 배달리뷰 항목이 없으므로 빈 문자열)
    delivery_review = ""

    # 주문번호 추출 - 반드시 해시 생성 전에 추출해야 함
    order_number = ""
    try:
        order_number_el = card_element.find_element(
            By.XPATH, 
            './/li[strong[contains(text(),"주문번호")]]/p'
        )
        if order_number_el:
            order_text = order_number_el.text.strip()
            # 주문번호 형식이 "0RWPXFㆍ2025-02-27(주문일)" 같은 형태라면
            # 첫 부분(0RWPXF)만 추출
            if 'ㆍ' in order_text:
                order_number = order_text.split('ㆍ')[0].strip()
            else:
                order_number = order_text
    except NoSuchElementException:
        order_number = ""
    except Exception as e:
        print(f"[handle_review_card] 주문번호 추출 중 오류: {str(e)}")
        order_number = ""

    print(f"[handle_review_card] => 작성자={author}, 별점={star_rating}, 리뷰내용={review_text[:20]}...")

    # 4. 날짜 추출
    review_date = extract_relative_date(driver, card_element)
    print(f"[handle_review_card] 추출된 리뷰 날짜: {review_date}")

    # 3. 리뷰 ID 생성 - 이제 모든 필요한 변수가 준비됨
    review_hash = generate_review_hash(store_code, author, review_text, order_number, review_date, star_rating)
    
    # 5. 중복 체크 및 재시도 횟수 확인, 날짜 기반 처리
    should_skip, retry_count, status = _check_duplicate_review(
        driver, store_code, platform_name, review_hash, author, review_text, review_date
    )

    # 스킵해야 하는 경우
    if should_skip:
        # 답변대기 상태가 반환되었으면 DB에 저장
        if status == "답변대기":
            _save_review_data(
                store_code=store_code,
                platform=platform_name,
                platform_code=platform_code,
                review_name=author,
                rating=star_rating,
                ordered_menu=order_menu,
                delivery_review=delivery_review,
                review_content=review_text,
                ai_response="",
                review_date=review_date,
                status="답변대기",  # 답변대기 상태로 저장
                store_name=store_nm,
                review_id=review_hash,
                retry_count=retry_count
            )
        return None
    
    # 6. 별점 정책 체크
    rating_key = f"rating_{star_rating}_reply"
    if star_rating < 1 or star_rating > 5 or not rule.get(rating_key, True):
        print(f"[handle_review_card] 별점({star_rating}) 자동답글 제외")
        _save_review_data(
            store_code=store_code,
            platform=platform_name,
            platform_code=platform_code,
            review_name=author,
            rating=star_rating,
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
    
    # 7. 신규 리뷰인 경우 처리 - 날짜 체크 제거 (모든 리뷰 즉시 처리)
    existing = supabase.table("reviews").select("*").eq("review_id", review_hash).execute()
    if not existing.data:
        # 리뷰 분석으로 사장님 확인 필요 여부 판단
        analysis_result = analyze_restaurant_review(review_text, star_rating, order_menu, delivery_review)
        
        # AI 답변 불가능 판정이면 사장님 확인 필요로 저장
        if not analysis_result['ai_reply']:
            print(f"[handle_review_card] AI 답변 불가 판정: {analysis_result['reason']}")
            _save_review_data(
                store_code=store_code,
                platform=platform_name,
                platform_code=platform_code,
                review_name=author,
                rating=star_rating,
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
    
    # 8. AI 답변 생성
    try:
        # 기존 리뷰가 '사장님 확인필요' 상태였는지 검사
        is_from_boss_review = existing.data and existing.data[0].get('response_status') == "사장님 확인필요"

        # 일반 리뷰(사장님 확인필요가 아닌 경우)는 추가 분석
        if not is_from_boss_review:
            analysis_result = analyze_restaurant_review(review_text, star_rating, order_menu, delivery_review)
            
            if not analysis_result['ai_reply']:
                print(f"[handle_review_card] AI 답변 불가 판정: {analysis_result['reason']}")
                _save_review_data(
                    store_code=store_code,
                    platform=platform_name,
                    platform_code=platform_code,
                    review_name=author,
                    rating=star_rating,
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
                    retry_count=retry_count + 1
                )
                return None

        # 9. AI 답변 생성
        store_info = {
            "store_code": store_code,
            "store_name": store_nm,
            "greeting_start": rule.get("greeting_start", "안녕하세요"),
            "greeting_end": rule.get("greeting_end", "감사합니다"),
            "role": rule.get("role", ""),
            "tone": rule.get("tone", ""),
            "max_length": rule.get("max_length", 300),
            "author": author,
            "rating": star_rating,
            "store_type": rule.get("store_type", "delivery_only")
        }
        
        # 개선된 답변 생성 함수 사용
        ai_reply = generate_ai_reply_with_retry(review_text, store_info)
        if not ai_reply:
            print("[handle_review_card] AI 답변 생성 실패")
            _save_review_data(
                store_code=store_code,
                platform=platform_name,
                platform_code=platform_code,
                review_name=author,
                rating=star_rating,
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

        # 10. 답변 품질 검사
        is_good_reply, _ = score_reply(ai_reply, review_text, author, star_rating, threshold=80)
        if not is_good_reply:
            print("[handle_review_card] AI 답변 품질 미달")
            _save_review_data(
                store_code=store_code,
                platform=platform_name,
                platform_code=platform_code,
                review_name=author,
                rating=star_rating,
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

        # 11. 댓글 등록 - 금지어 처리 추가
        max_retries = 2  # 금지어 발견 시 최대 재시도 횟수
        current_retry = 0

        while current_retry <= max_retries:
            # 답변 내용 간소화 시도 - 이모지와 특수문자 제거
            simple_reply = unicodedata.normalize('NFKD', ai_reply)
            simple_reply = re.sub(r'[^\w\s.,!?]', '', simple_reply)  # 특수문자 제거
            
            # 안전 조치: 고객 이름을 '고객님'으로 변경
            if author in simple_reply:
                simple_reply = simple_reply.replace(author, "고객님")
            
            # 댓글 등록 시도 (수정된 답변 사용)
            is_success, prohibited_word = click_and_submit_comment(
                driver, 
                card_element, 
                ai_reply,
                author
            )

            if is_success is True:
                # 성공 시 DB 저장 및 함수 종료
                print("[handle_review_card] 댓글 등록 성공!")
                _save_review_data(
                    store_code=store_code,
                    platform=platform_name,
                    platform_code=platform_code,
                    review_name=author,
                    rating=star_rating,
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
                
            elif is_success == "API_ERROR":
                # API 오류 처리
                error_msg = "외부 API 호출 실패"
                print(f"[handle_review_card] {error_msg}")
                
                save_error_screenshot(driver, store_code, "API호출실패")
                save_error_log_to_supabase(
                    category="답글 실패",
                    platform=platform_name,
                    store_code=store_code,
                    error_type="API 호출 실패",
                    error_message=error_msg
                )
                
                _save_review_data(
                    store_code=store_code,
                    platform=platform_name,
                    platform_code=platform_code,
                    review_name=author,
                    rating=star_rating,
                    ordered_menu=order_menu,
                    delivery_review=delivery_review,
                    review_content=review_text,
                    ai_response=ai_reply,
                    review_date=review_date,
                    status="API오류",
                    store_name=store_nm,
                    review_id=review_hash,
                    retry_count=retry_count + 1
                )
                return None
            
            elif is_success == "PROHIBITED_NAME":
                # 작성자 이름 관련 금지어 발견 시
                print(f"[handle_review_card] 작성자 이름({author}) 관련 금지어 발견. '고객님'으로 대체하여 재시도")
                
                # 원본 AI 답변에서 작성자 이름을 '고객님'으로 교체
                modified_reply = ai_reply.replace(author, "고객님")
                if author + "님" in ai_reply:
                    modified_reply = modified_reply.replace(author + "님", "고객님")
                
                # 수정된 답변으로 재시도
                ai_reply = modified_reply
                current_retry += 1
                print(f"[handle_review_card] 이름 수정 후 재시도 ({current_retry}/{max_retries})")
                time.sleep(2)  # 잠시 대기 후 재시도
                continue
                
            elif is_success == "PROHIBITED_CONTENT":
                # 내용 관련 금지어 발견 시 새로운 답변 생성
                prohibited_info = f"'{prohibited_word}'" if prohibited_word else "내용 관련 금지어"
                print(f"[handle_review_card] {prohibited_info} 발견. 새로운 답변 생성 시도")
                
                try:
                    # 금지어 없는 새 답변 생성 시도 - 감지된 금지어 정보 포함
                    store_info = {
                        "store_code": store_code,
                        "store_name": store_nm,
                        "greeting_start": rule.get("greeting_start", "안녕하세요"),
                        "greeting_end": rule.get("greeting_end", "감사합니다"),
                        "role": rule.get("role", ""),
                        "tone": rule.get("tone", ""),
                        "max_length": rule.get("max_length", 300),
                        "author": "고객님",  # 작성자를 '고객님'으로 설정
                        "rating": star_rating,
                        "store_type": rule.get("store_type", "delivery_only"),
                        "prohibited_words": rule.get("prohibited_words", []),
                        "detected_prohibited_word": prohibited_word  # 감지된 금지어 추가
                    }
                    
                    # 금지어 없는 답변 생성 함수 호출
                    ai_reply = generate_prohibited_free_reply(review_text, store_info)
                    
                    if not ai_reply:
                        raise Exception("금지어 없는 답변 생성 실패")
                        
                    current_retry += 1
                    print(f"[handle_review_card] 새 답변 생성 후 재시도 ({current_retry}/{max_retries})")
                    time.sleep(2)  # 잠시 대기 후 재시도
                    continue
                    
                except Exception as e:
                    print(f"[handle_review_card] 금지어 없는 답변 생성 실패: {str(e)}")
                    # 실패 시 에러 로깅 및 저장
                    error_msg = f"금지어 처리 중 오류: {str(e)}"
                    print(f"[handle_review_card] {error_msg}")
                    
                    save_error_screenshot(driver, store_code, "금지어처리실패")
                    save_error_log_to_supabase(
                        category="답글 실패",
                        platform=platform_name,
                        store_code=store_code,
                        error_type="금지어 처리 실패",
                        error_message=error_msg
                    )
                    
                    _save_review_data(
                        store_code=store_code,
                        platform=platform_name,
                        platform_code=platform_code,
                        review_name=author,
                        rating=star_rating,
                        ordered_menu=order_menu,
                        delivery_review=delivery_review,
                        review_content=review_text,
                        ai_response=ai_reply,
                        review_date=review_date,
                        status="금지어",
                        store_name=store_nm,
                        review_id=review_hash,
                        retry_count=retry_count + 1
                    )
                    return None
            
            else:  # 다른 실패 유형
                # 기존 에러 처리 로직
                error_msg = "댓글 등록 실패"
                print(f"[handle_review_card] {error_msg}")
                    
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
                    rating=star_rating,
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

         # 모든 시도 후에도 실패한 경우
        if current_retry > max_retries:
            error_msg = f"금지어 처리 최대 시도 횟수({max_retries}) 초과"
            print(f"[handle_review_card] {error_msg}")
            
            save_error_screenshot(driver, store_code, "금지어시도초과")
            save_error_log_to_supabase(
                category="답글 실패",
                platform=platform_name,
                store_code=store_code,
                error_type="금지어 시도 초과",
                error_message=error_msg
            )
            
            _save_review_data(
                store_code=store_code,
                platform=platform_name,
                platform_code=platform_code,
                review_name=author,
                rating=star_rating,
                ordered_menu=order_menu,
                delivery_review=delivery_review,
                review_content=review_text,
                ai_response=ai_reply,
                review_date=review_date,
                status="금지어",
                store_name=store_nm,
                review_id=review_hash,
                retry_count=retry_count + 1
            )
            return None
    
    except Exception as e:
        error_msg = str(e)
        print(f"[handle_review_card] 댓글 등록 중 오류: {error_msg}")
        
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
            rating=star_rating,
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
###################################################
# 14) 리뷰 페이지 전체 처리 함수
###################################################
def process_reviews_on_page(driver, store_code, platform_name, platform_code, store_nm, rule):
    """리뷰 페이지에서 모든 리뷰 카드를 처리하는 함수 - 최종 확인 로직 추가"""
    global processed_reviews_in_session
    print("[process_reviews_on_page] 시작")
    
    total_processed = 0
    max_attempts = 3  # 전체 검사 최대 시도 횟수
    
    for attempt in range(1, max_attempts + 1):
        print(f"[process_reviews_on_page] 검사 시도 {attempt}/{max_attempts}")
        
        # 초기 페이지 설정 확인 및 복구
        if not restore_page_settings(driver, store_code, store_nm, platform_code):
            print(f"[process_reviews_on_page] 페이지 설정 복구 실패, 다시 시도")
            continue
        
        new_reviews_processed = 0  # 이번 시도에서 처리된 새 리뷰 수
        current_page = 1
        
        # 모든 페이지 리뷰 처리
        while True:
            print(f"[process_reviews_on_page] 페이지 {current_page} 처리 중")
            
            try:
                # 현재 페이지의 리뷰 카드 가져오기
                rows = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.XPATH, '//tr'))
                )
                
                if not rows:
                    print("[process_reviews_on_page] 페이지에 리뷰 없음")
                    break
                
                # 페이지 내 리뷰 처리
                page_processed = 0
                
                # 이미 처리된 리뷰 스킵을 위한 ID 목록 미리 생성
                review_ids_on_page = []
                for row in rows:
                    try:
                        # 답글 버튼 확인
                        comment_btns = row.find_elements(By.XPATH, ".//button[contains(text(),'사장님 댓글 등록하기')]")
                        if comment_btns:
                            review_id = get_review_identifier(row, store_code)
                            review_ids_on_page.append((row, review_id))
                    except:
                        continue
                
                # 이미 처리된 리뷰 제외하고 처리할 리뷰만 선택
                reviews_to_process = []
                for row, review_id in review_ids_on_page:
                    if review_id not in processed_reviews_in_session:
                        reviews_to_process.append((row, review_id))
                        
                print(f"[process_reviews_on_page] 처리할 리뷰 수: {len(reviews_to_process)}/{len(review_ids_on_page)}")
                
                # 선택된 리뷰 처리
                for row, review_id in reviews_to_process:
                    try:
                        # 리뷰 처리 전 페이지 상태 확인
                        if not check_page_state(driver, store_code):
                            raise Exception("페이지 상태 미일치")
                            
                        # 리뷰 처리
                        result = handle_review_card(
                            driver=driver,
                            store_code=store_code,
                            platform_name=platform_name,
                            platform_code=platform_code,
                            store_nm=store_nm,
                            card_element=row,
                            rule=rule
                        )
                        
                        # 세션에 처리 완료 표시
                        processed_reviews_in_session.add(review_id)
                        page_processed += 1
                        total_processed += 1
                        new_reviews_processed += 1
                        
                        # 리뷰 처리 후 페이지 상태 복구
                        if not restore_page_settings(driver, store_code, store_nm, platform_code):
                            print("[process_reviews_on_page] 페이지 상태 복구 실패, 건너뜀")
                            break
                    
                    except Exception as e:
                        print(f"[process_reviews_on_page] 리뷰 처리 중 오류: {str(e)}")
                        # 페이지 상태 복구 시도
                        if not restore_page_settings(driver, store_code, store_nm, platform_code):
                            print("[process_reviews_on_page] 오류 후 페이지 복구 실패, 건너뜀")
                            break
                
                # 현재 페이지 처리 결과 보고
                if page_processed > 0:
                    print(f"[process_reviews_on_page] 페이지 {current_page}에서 {page_processed}개 처리됨")
                
                # 다음 페이지로 이동
                if not go_to_next_page(driver):
                    print("[process_reviews_on_page] 더 이상 페이지 없음")
                    break
                
                current_page += 1
                
            except Exception as e:
                print(f"[process_reviews_on_page] 페이지 처리 오류: {str(e)}")
                save_error_screenshot(driver, store_code, f"페이지오류_{current_page}")
                
                # 페이지 상태 복구 시도
                if not restore_page_settings(driver, store_code, store_nm, platform_code):
                    print("[process_reviews_on_page] 복구 실패, 다시 시도")
                    break
        
        # 새 리뷰가 없으면 시도 종료
        if new_reviews_processed == 0:
            print(f"[process_reviews_on_page] 새로운 리뷰 없음 - 완료")
            break
        
        print(f"[process_reviews_on_page] 시도 {attempt}에서 {new_reviews_processed}개 리뷰 처리됨")
    
    # 최종 확인: 조회 버튼 클릭 후 아직 처리해야 할 리뷰가 있는지 다시 확인
    print("[process_reviews_on_page] 모든 리뷰 처리 완료 후 최종 확인 시작")
    try:
        # 조회 버튼 클릭
        try:
            search_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(), '조회')]]"))
            )
            driver.execute_script("arguments[0].click();", search_button)
            time.sleep(3)
            print("[process_reviews_on_page] 최종 확인: 조회 버튼 클릭 성공")
        except Exception as e:
            print(f"[process_reviews_on_page] 최종 확인: 조회 버튼 클릭 실패 - {str(e)}")
            
        # 미처리된 리뷰 확인
        final_check_count = 0
        current_page = 1
        
        while True:
            # 페이지의 리뷰 카드 가져오기
            rows = WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.XPATH, '//tr'))
            )
            
            if not rows:
                print(f"[process_reviews_on_page] 최종 확인: 페이지 {current_page}에 리뷰 없음")
                break
            
            # 처리해야 할 리뷰 있는지 확인
            missing_reviews = []
            for row in rows:
                try:
                    # 답글 버튼 확인
                    comment_btns = row.find_elements(By.XPATH, ".//button[contains(text(),'사장님 댓글 등록하기')]")
                    if comment_btns:
                        # 처리 누락된 리뷰 발견
                        try:
                            author_el = row.find_element(By.XPATH, './/div[contains(@class,"css-hdvjju")]/b')
                            author = author_el.text.strip() if author_el else "Unknown"
                            review_id = get_review_identifier(row, store_code)
                            
                            # 이미 처리했던 리뷰인지 확인
                            if review_id not in processed_reviews_in_session:
                                print(f"[process_reviews_on_page] 최종 확인: 미처리 리뷰 발견 - {author}")
                                missing_reviews.append((row, review_id))
                        except Exception as e:
                            print(f"[process_reviews_on_page] 최종 확인: 리뷰 정보 추출 실패 - {str(e)}")
                except Exception as e:
                    continue
            
            # 미처리 리뷰 처리
            for row, review_id in missing_reviews:
                try:
                    result = handle_review_card(
                        driver=driver,
                        store_code=store_code,
                        platform_name=platform_name,
                        platform_code=platform_code,
                        store_nm=store_nm,
                        card_element=row,
                        rule=rule
                    )
                    
                    # 세션에 처리 완료 표시
                    processed_reviews_in_session.add(review_id)
                    final_check_count += 1
                    total_processed += 1
                    
                    # 페이지 상태 복구
                    if not restore_page_settings(driver, store_code, store_nm, platform_code):
                        print("[process_reviews_on_page] 최종 확인: 페이지 상태 복구 실패")
                        break
                except Exception as e:
                    print(f"[process_reviews_on_page] 최종 확인: 리뷰 처리 오류 - {str(e)}")
            
            # 다음 페이지로 이동
            if not go_to_next_page(driver):
                print("[process_reviews_on_page] 최종 확인: 더 이상 페이지 없음")
                break
            
            current_page += 1
        
        if final_check_count > 0:
            print(f"[process_reviews_on_page] 최종 확인 결과: {final_check_count}개 추가 리뷰 처리됨")
        else:
            print("[process_reviews_on_page] 최종 확인 결과: 모든 리뷰 정상 처리됨")
    
    except Exception as e:
        print(f"[process_reviews_on_page] 최종 확인 중 오류: {str(e)}")
    
    print(f"[process_reviews_on_page] 총 {total_processed}개 리뷰 처리 완료")
    return total_processed

def go_to_next_page(driver):
    """다음 페이지로 이동"""
    try:
        next_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((
                By.XPATH, 
                "//button[contains(@class, 'pagination-btn next-btn') and @data-at='next-btn']"
            ))
        )

        class_attr = next_button.get_attribute('class')
        if 'disabled' in class_attr:
            print("[go_to_next_page] 다음 페이지 버튼 비활성화됨 (마지막 페이지)")
            return False
        
        driver.execute_script("arguments[0].click();", next_button)
        time.sleep(3)  # 페이지 로딩 대기
        print("[go_to_next_page] 다음 페이지로 이동 성공")
        return True
    
    except TimeoutException:
        print("[go_to_next_page] 다음 페이지 버튼 없음")
        return False
    except Exception as e:
        print(f"[go_to_next_page] 페이지 이동 오류: {str(e)}")
        return False

###################################################
# 15) 매장 선택 함수
###################################################
def navigate_to_review_management(driver, store_code, store_name, platform_code):
    """리뷰 관리 페이지로 이동하고 가게 선택"""
    try:
        # 모달 닫기 시도
        try:
            modal_close_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@class='dialog-modal-wrapper__body--close-button']"))
            )
            modal_close_btn.click()
            print("[navigate_to_review_management] 모달 닫기 성공")
        except TimeoutException:
            print("[navigate_to_review_management] 닫을 모달 없음")
            pass

        # 페이지 완전 로딩 대기
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script('return document.readyState') == 'complete'
        )

        # 리뷰 관리 링크 클릭
        review_link = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(@href, '/merchant/management/reviews')]"))
        )
        driver.execute_script("arguments[0].click();", review_link)
        print("[navigate_to_review_management] 리뷰 관리 페이지 이동")
        time.sleep(5)

        # 매장 드롭다운 체크 및 선택
        if not verify_store_code(driver, platform_code, store_name):
            print(f"[navigate_to_review_management] 가게코드 {platform_code} 선택 실패")
            return False
        
        # 날짜 범위 설정
        try:
            date_dropdown = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//div[input[@name='startDate']]/div"))
            )
            driver.execute_script("arguments[0].click();", date_dropdown)
            time.sleep(2)

            one_month_option = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//label[.//span[text()='1개월']]"))
            )
            driver.execute_script("arguments[0].click();", one_month_option)
            time.sleep(2)
            print("[navigate_to_review_management] 날짜 범위 설정: 1개월")
        except Exception as e:
            print(f"[navigate_to_review_management] 날짜 범위 설정 실패: {e}")
            save_error_screenshot(driver, store_code, "날짜설정실패")
            return False

        # 미답변 탭 클릭
        if not click_unanswered_tab(driver, store_code):
            print(f"[navigate_to_review_management] 미답변 탭 클릭 실패")
            return False
        
        print(f"[navigate_to_review_management] 리뷰 페이지 설정 완료 - {store_name}")
        return True

    except Exception as e:
        error_msg = f"[navigate_to_review_management] 리뷰 페이지 이동 오류: {e}"
        print(error_msg)
        save_error_screenshot(driver, store_code, "리뷰페이지이동실패")
        save_error_log_to_supabase(
            category="오류",
            platform="쿠팡잇츠",
            store_code=store_code,
            error_type="리뷰 페이지 이동 실패",
            error_message=error_msg
        )
        return False

def verify_store_code(driver, platform_code, store_name):
    """
    화면 상의 가게코드가 우리가 사용하려는 platform_code와 일치하는지 확인.
    드롭다운에서 찾을 수 없으면 False
    """
    try:
        store_selector = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".button"))
        )
        
        button_text = store_selector.find_element(By.CSS_SELECTOR, "div > div").text
        match = re.search(r'\((\d+)\)', button_text)
        if not match:
            print(f"[verify_store_code] 현재 선택된 가게코드를 찾을 수 없음")
            return False
            
        current_code = match.group(1)
        if current_code == str(platform_code):
            print(f"[verify_store_code] 가게코드 확인 완료: {current_code}")
            return True
        
        # 드롭다운 열고 찾기
        store_selector.click()
        time.sleep(2)
        
        options = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul.options li"))
        )
        for option in options:
            option_text = option.text
            code_match = re.search(r'\((\d+)\)', option_text)
            if code_match and code_match.group(1) == str(platform_code):
                driver.execute_script("arguments[0].click();", option)
                time.sleep(2)
                print(f"[verify_store_code] 드롭다운에서 가게코드 {platform_code} 선택 완료")
                return True

        print(f"[verify_store_code] 드롭다운에서 가게코드 {platform_code}를 찾을 수 없습니다.")
        return False
        
    except Exception as e:
        print(f"[verify_store_code] 가게코드 확인 중 오류: {e}")
        return False

def click_unanswered_tab(driver, store_code):
    """미답변 탭 클릭"""
    try:
        for attempt in range(3):
            try:
                unanswered_tab = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((
                        By.XPATH,
                        "//div[contains(@class, 'e1fz5w2d5')]//span[text()='미답변']"
                    ))
                )
                
                parent_tab = unanswered_tab.find_element(By.XPATH, "./ancestor::div[contains(@class, 'e1fz5w2d5')]")
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});", parent_tab)
                time.sleep(1)
                
                driver.execute_script("arguments[0].click();", parent_tab)
                time.sleep(2)
                
                # 클릭 확인 (강조 상태 체크)
                is_highlighted = driver.execute_script("""
                    var tabs = document.querySelectorAll('div.e1fz5w2d5');
                    for (var tab of tabs) {
                        var numberElement = tab.querySelector('b.css-1k8kvzj');
                        if (numberElement) {
                            return true;
                        }
                    }
                    return false;
                """)
                
                if is_highlighted:
                    print(f"[click_unanswered_tab] 미답변 탭 클릭 성공 (시도 {attempt + 1})")
                    
                    # 조회 버튼 클릭 (필요시)
                    try:
                        search_button = WebDriverWait(driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(), '조회')]]"))
                        )
                        driver.execute_script("arguments[0].click();", search_button)
                        time.sleep(3)
                        print("[click_unanswered_tab] 조회 버튼 클릭 성공")
                    except:
                        print("[click_unanswered_tab] 조회 버튼 없거나 클릭 불필요")
                    
                    return True
                else:
                    print(f"[click_unanswered_tab] 시도 {attempt + 1}: 파란색 강조 미적용")
            except Exception as e:
                print(f"[click_unanswered_tab] 시도 {attempt + 1} 실패: {str(e)}")
            time.sleep(2)
        
        print("[click_unanswered_tab] 미답변 탭 클릭 실패")
        save_error_screenshot(driver, store_code, "미답변탭클릭실패")
        return False
        
    except Exception as e:
        print(f"[click_unanswered_tab] 미답변 탭 클릭 중 오류: {str(e)}")
        save_error_screenshot(driver, store_code, "탭클릭오류")
        return False

###################################################
# 16) 로그아웃 함수
###################################################
def logout_and_move_to_next(driver):
    """로그아웃하고 다음 계정으로 넘어가는 함수"""
    try:
        # 사용자 메뉴 클릭
        try:
            user_menu = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'UserMenu')]"))
            )
            driver.execute_script("arguments[0].click();", user_menu)
            time.sleep(1)
            
            # 로그아웃 메뉴 클릭
            logout_option = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//div[contains(text(), '로그아웃')]"))
            )
            driver.execute_script("arguments[0].click();", logout_option)
            time.sleep(2)
            
            print("[logout_and_move_to_next] 로그아웃 완료")
            return True
        except:
            # 메뉴를 통한 로그아웃 실패시 URL 직접 이동
            driver.get("https://store.coupangeats.com/merchant/logout")
            time.sleep(2)
            print("[logout_and_move_to_next] URL 통한 로그아웃 시도")
            return True
            
    except Exception as e:
        print(f"[logout_and_move_to_next] 로그아웃 중 문제: {str(e)}")
        # 오류 발생해도 다음 계정 처리 위해 새 창으로 시작
        return False

###################################################
# 17) 메인 실행 함수
###################################################
def run_automation():
    """자동화 실행 버튼 클릭 시 실행되는 메인 함수"""
    global processed_reviews_in_session
    processed_reviews_in_session = set()  # 세션 초기화
    
    # 로그 디렉토리 생성
    log_dir = '쿠팡_로그'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 로그 파일 경로 생성
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join(log_dir, f"coupang_log_{timestamp}.txt")
    
    # 로그 파일 열기
    original_stdout = sys.stdout
    log_file = open(log_file_path, 'w', encoding='utf-8')
    sys.stdout = LogWriter(original_stdout, log_file)
    
    try:
        print(f"=== 쿠팡이츠 자동화 로그 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        
        # 경로 지정 확인
        config = load_config()
        driver_path = config.get('chromedriver_path', '')
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
                messagebox.showerror("오류", "범위를 입력하세요 (예: STORE0001, STORE0005)")
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
                messagebox.showerror("오류", "범위 형식이 잘못되었습니다. 예: STORE0001, STORE0005")
                return
        else:
            print("[run_automation] 전체 실행 모드")

        # 인증정보별 그룹화
        creds_group = group_by_credentials(platform_data)

        # 계정별 처리
        for (pid, ppw), rules_list in creds_group.items():
            print(f"\n[run_automation] 로그인 계정 ID={pid}, 매장수={len(rules_list)}")
            if not rules_list:
                continue

            # 브라우저 옵션 설정
            options = uc.ChromeOptions()
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--start-maximized")

            # 드라이버 시작
            print("[run_automation] 드라이버 시작")
            try:
                driver = uc.Chrome(options=options)
                
                # 1) 로그인
                login_success, driver = login_to_coupang(driver, pid, ppw, rules_list[0]["store_code"], "쿠팡잇츠", options)
                if not login_success:
                    print(f"[run_automation] ID={pid} 로그인 실패 => 다음 계정으로 넘어감")
                    driver.quit()
                    continue
                    
                # 로그인 성공 시 매장별 처리
                for rule in rules_list:
                    store_code = rule["store_code"]
                    platform_code = rule["platform_code"]
                    store_nm = rule["store_name"]
                    print(f"[run_automation] 매장처리 시작 store_code={store_code}, platform=쿠팡잇츠, code={platform_code}")
                    
                    # 새창 체크 및 닫기
                    check_and_close_new_windows(driver, store_code)
                    
                    # 리뷰 페이지로 이동
                    if not navigate_to_review_management(driver, store_code, store_nm, platform_code):
                        print(f"[run_automation] {store_code} 리뷰 페이지 이동 실패 => 다음 매장으로 넘어감")
                        continue
                    
                    # 리뷰 처리
                    process_reviews_on_page(driver, store_code, "쿠팡잇츠", platform_code, store_nm, rule)
                    
                    # 다음 매장 처리 전 잠시 대기
                    time.sleep(2)
                
                # 드라이버 종료
                try:
                    driver.quit()
                    print("[run_automation] 드라이버 종료")
                except:
                    pass
                
            except Exception as e:
                print(f"[run_automation] 계정 처리 중 전체 오류: {str(e)}")
                try:
                    if driver:
                        driver.quit()
                except:
                    pass

        # 모든 작업 완료 후 메시지
        print(f"=== 쿠팡이츠 자동화 로그 종료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        messagebox.showinfo("완료", f"쿠팡이츠 리뷰 자동화 처리가 완료되었습니다.\n로그 파일: {log_file_path}")
    
    except Exception as e:
        print(f"[run_automation] 프로그램 실행 중 치명적 오류: {str(e)}")
        messagebox.showerror("오류", f"자동화 실행 중 오류가 발생했습니다.\n오류: {str(e)}")
    
    finally:
        # 표준 출력 복원 및 로그 파일 닫기
        sys.stdout = original_stdout
        log_file.close()
        print(f"로그 파일이 저장되었습니다: {log_file_path}")

# 화면과 파일에 동시에 출력하기 위한 클래스 (run_automation 함수 바로 위에 추가)
class LogWriter:
    def __init__(self, terminal, log_file):
        self.terminal = terminal
        self.log_file = log_file
        
    def write(self, message):
        self.terminal.write(message)
        self.log_file.write(message)
        
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()

# GUI 설정
root = tk.Tk()
root.title("쿠팡이츠 리뷰 자동화")

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

range_label = tk.Label(frame_mode, text="StoreCode 범위 (예: STORE00001, STORE00003)")
range_label.grid(row=1, column=0, columnspan=2, sticky="w")

range_entry = tk.Entry(frame_mode, width=30)
range_entry.grid(row=2, column=0, columnspan=2, pady=5, sticky="w")

# 실행 버튼
btn_run = tk.Button(root, text="자동화 실행", command=run_automation)
btn_run.pack(pady=20)

if __name__ == "__main__":
    # 설정 파일에서 드라이버 경로 로드
    config = load_config()
    driver_path = config.get('chromedriver_path', '')
    
    # 드라이버 경로가 설정되었으면 UI에 표시
    if driver_path:
        driver_label.config(text=f"크롬드라이버 경로: {driver_path}")
    
    # GUI 메인 루프 시작
    root.mainloop()