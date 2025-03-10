import os
import json
import time
import traceback
import logging
from datetime import datetime, timedelta
import hashlib
import re
import unicodedata
import tkinter as tk
from tkinter import filedialog, messagebox

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException, 
    ElementClickInterceptedException
)

# Supabase & OpenAI 설정
from supabase import create_client, Client
import openai
from dotenv import load_dotenv

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("yogiyo_automation.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# 환경 설정
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = openai  # OpenAI 클라이언트

CONFIG_FILE = 'config_yogiyo.json'

# 세션 내 처리된 리뷰 관리
processed_reviews_in_session = set()

###################################################################
# 1) 설정 파일 관련 함수
###################################################################
def load_config():
    """
    config_yogiyo.json 에서 chromedriver_path 등의 정보를 불러온다
    """
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_config(cfg):
    """설정 저장"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)

config = load_config()
driver_path = config.get('chromedriver_path', '')

# 1. 날짜 추출 함수 추가
def extract_date_from_html(element):
    """
    리뷰 HTML 요소에서 날짜를 추출
    - '분 전', '시간 전' 등의 상대적 시간은 오늘 날짜로 변환
    - 'YYYY.MM.DD' 형식은 그대로 사용
    """
    try:
        # 날짜 요소 찾기 (Typography 클래스를 가진 p 태그)
        date_els = element.find_elements(By.CSS_SELECTOR, "p.Typography__StyledTypography-sc-r9ksfy-0.jwoVKl")
        
        # 여러 요소가 있을 수 있으므로 적절한 것 선택
        date_text = ""
        for el in date_els:
            text = el.text.strip()
            # 날짜 형식인지 확인 (YYYY.MM.DD)
            if re.match(r'\d{4}\.\d{2}\.\d{2}', text):
                date_text = text
                break
        
        # 날짜 요소를 찾지 못한 경우
        if not date_text:
            return datetime.now().strftime("%Y-%m-%d")
        
        # '분 전', '시간 전' 확인
        if '분 전' in date_text or '시간 전' in date_text:
            return datetime.now().strftime("%Y-%m-%d")
        
        # 'YYYY.MM.DD' 형식 처리
        elif re.match(r'\d{4}\.\d{2}\.\d{2}', date_text):
            # '2025.02.21' 형식을 '2025-02-21' 형식으로 변환
            return date_text.replace('.', '-')
        
        # 그 외 경우 오늘 날짜 반환
        else:
            return datetime.now().strftime("%Y-%m-%d")
            
    except Exception as e:
        logging.error(f"[extract_date_from_html] 날짜 추출 오류: {e}")
        return datetime.now().strftime("%Y-%m-%d")

###################################################################
# 2) Supabase에서 요기요용 데이터 불러오기
###################################################################
def fetch_yogiyo_data():
    """
    platform_reply_rules 테이블에서 요기요 데이터를 가져옴
    """
    try:
        response = (
            supabase
            .table("platform_reply_rules")
            .select("*")
            .eq("platform", "요기요")
            .execute()
        )

        if not response.data:
            logging.warning("[fetch_yogiyo_data] 요기요용 platform_reply_rules 데이터가 없습니다.")
            return []

        rows = []
        for item in response.data:
            row = {
                "store_code": item["store_code"],
                "store_name": item.get("store_name", ""),
                "platform_code": item.get("platform_code"),
                "platform_id": item.get("platform_id"),
                "platform_pw": item.get("platform_pw"),
                "greeting_start": item.get("greeting_start"),
                "greeting_end": item.get("greeting_end"),
                "role": item.get("role"),
                "tone": item.get("tone"),
                "prohibited_words": item.get("prohibited_words"),
                "max_length": item.get("max_length", 350),
                "rating_5_reply": item.get("rating_5_reply", True),
                "rating_4_reply": item.get("rating_4_reply", True),
                "rating_3_reply": item.get("rating_3_reply", True),
                "rating_2_reply": item.get("rating_2_reply", True),
                "rating_1_reply": item.get("rating_1_reply", True),
                "store_type": item.get("store_type", "delivery_only")
            }
            rows.append(row)

        logging.info(f"[fetch_yogiyo_data] 요기요 매장 데이터 {len(rows)}개 로드 완료")
        return rows
    except Exception as e:
        logging.error(f"[fetch_yogiyo_data] 오류: {e}")
        return []

# 2. 리뷰-답변 일치 검증 함수 추가
def verify_review_reply_match(driver, review_element, review_data):
    """
    답글이 올바른 리뷰에 작성되는지 위치만 확인하는 함수
    """
    try:
        # 1. 현재 작업 중인 리뷰 요소에서 작성자명 가져오기
        author_element = review_element.find_element(By.CSS_SELECTOR, "h6.Typography__StyledTypography-sc-r9ksfy-0")
        current_author = author_element.text.strip()
        
        # 2. 저장된 review_data의 작성자와 비교
        expected_author = review_data['author']
        
        # 3. 작성자명이 일치하는지 확인
        if current_author != expected_author:
            return False, f"작성자 불일치: 예상={expected_author}, 실제={current_author}"
        
        # 4. textarea가 현재 리뷰 요소 내부에 있는지 확인
        textarea = review_element.find_elements(By.CSS_SELECTOR, "textarea.ReviewReply__CustomTextarea-sc-1536a88-4")
        if not textarea:
            return False, "댓글 입력창을 찾을 수 없음"
            
        return True, ""
        
    except Exception as e:
        logging.error(f"[verify_review_reply_match] 검증 오류: {e}")
        return False, f"검증 중 오류 발생: {e}"

###################################################################
# 3) 오류 로깅 및 스크린샷
###################################################################
def save_error_log_to_supabase(
    category: str,
    store_code: str,
    error_type: str,
    error_message: str,
    stack_trace: str = ""
):
    """
    error_logs 테이블에 오류 기록
    """
    try:
        # 매장명 조회
        store_query = supabase.table("platform_reply_rules") \
                             .select("store_name") \
                             .eq("store_code", store_code) \
                             .eq("platform", "요기요") \
                             .execute()
        store_name = store_query.data[0].get("store_name", "") if store_query.data else ""

        data = {
            "store_code": store_code,
            "category": category,
            "platform": "요기요",
            "error_type": error_type,
            "error_message": error_message,
            "stack_trace": stack_trace,
            "occurred_at": datetime.now().isoformat(),
            "store_name": store_name
        }
        res = supabase.table("error_logs").insert(data).execute()
        logging.info(f"[save_error_log_to_supabase] 에러 로그 저장: {store_code}/{error_type}")
    except Exception as ex:
        logging.error(f"[save_error_log_to_supabase] 에러 로그 저장 오류: {ex}")

def take_screenshot(driver, store_code, error_type):
    """
    오류 발생 시 스크린샷
    """
    try:
        shot_dir = "요기요_스크린샷"
        os.makedirs(shot_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{store_code}_{error_type}_{ts}.png"
        path = os.path.join(shot_dir, filename)
        driver.save_screenshot(path)
        logging.info(f"[take_screenshot] 스크린샷 저장: {path}")
        return path
    except Exception as ex:
        logging.error(f"[take_screenshot] 스크린샷 저장 실패: {ex}")
        return None

###################################################################
# 4) 리뷰 해시 생성 및 중복 체크
###################################################################
def generate_review_hash(store_code: str, author: str, review_text: str) -> str:
    """
    store_code + author(닉네임) + review_text를 합쳐 md5 해시를 생성
    """
    base_str = f"{store_code}_{author}_{review_text}"
    hash_val = hashlib.md5(base_str.encode("utf-8")).hexdigest()
    logging.info(f"[generate_review_hash] hash={hash_val[:8]}...")
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
        logging.error(f"[extract_date_from_review_id] 날짜 추출 오류: {e}, ID: {review_id}")
        return datetime.now().date().strftime("%Y-%m-%d")

def _check_duplicate_review(driver, store_code, review_hash, author, review_text):
    """
    중복 리뷰 체크 및 재처리 판단 로직
    - 상태별 다른 처리:
       - "답변완료": 다시 처리하지 않음
       - "답변대기": 2일 이상 경과한 경우 처리
       - "사장님 확인필요": 4일 이상 경과한 경우 처리
       - 그 외: 항상 재처리
    """
    try:
        # 이미 세션에서 처리했는지 확인
        if review_hash in processed_reviews_in_session:
            logging.info(f"[_check_duplicate_review] 세션 내 중복 처리 스킵: {review_hash[:8]}")
            return True
            
        today = datetime.now().date()
        
        # 기존 리뷰 조회
        existing = supabase.table("reviews").select("*").eq("review_id", review_hash).execute()
        
        if not existing.data:
            # 새 리뷰 - 일단 저장만 하고 처리는 나중에
            return False
            
        record = existing.data[0]
        status = record.get('response_status', '')
        record_date_str = record.get('review_date', '')
        retry_count = record.get('retry_count', 0)
        
        if retry_count >= 3:
            logging.info(f"[_check_duplicate_review] 재시도 횟수 초과 ({retry_count}/3): {review_hash[:8]}")
            return True  # 처리 중단
        
        # 날짜 차이 계산
        try:
            if record_date_str:
                record_date = datetime.strptime(record_date_str, "%Y-%m-%d").date()
                days_diff = (today - record_date).days
            else:
                days_diff = 0
        except ValueError as e:
            logging.error(f"[날짜변환오류] {record_date_str}: {e}")
            days_diff = 0
            
        if status == "답변완료":
            # 이미 답변 완료된 리뷰 - 로그만 남기고 스킵
            err_msg = f"이미 답변 완료된 리뷰 재시도: {author}, {review_text[:20]}..."
            save_error_log_to_supabase(
                category="중복처리",
                store_code=store_code,
                error_type="답변성공 등록 오류 재시도",
                error_message=err_msg,
                stack_trace=""
            )
            return True  # 스킵
            
        elif status == "답변대기":
            if days_diff < 2:
                # 2일 미만인 경우 스킵
                logging.info(f"[_check_duplicate_review] 최근({days_diff}일 전) '답변대기' 리뷰 스킵: {review_hash[:8]}")
                return True
            else:
                # 2일 이상 지난 경우 재처리
                logging.info(f"[_check_duplicate_review] {days_diff}일 경과된 '답변대기' 리뷰 처리: {review_hash[:8]}")
                return False
                
        elif status == "사장님 확인필요":
            if days_diff < 4:
                # 4일 미만인 경우 스킵
                logging.info(f"[_check_duplicate_review] 최근({days_diff}일 전) '사장님 확인필요' 리뷰 스킵: {review_hash[:8]}")
                return True
            else:
                # 4일 이상 지난 경우 재처리
                logging.info(f"[_check_duplicate_review] {days_diff}일 경과된 '사장님 확인필요' 리뷰 재처리: {review_hash[:8]}")
                return False
        
        else:
            # 실패, 미답변 등 다른 상태는 재처리
            logging.info(f"[_check_duplicate_review] 기존 미완료 리뷰({status}) 재처리: {review_hash[:8]}")
            return False
            
    except Exception as e:
        logging.error(f"[_check_duplicate_review] 중복 체크 중 오류: {e}")
        return False

###################################################################
# 5) WebDriver 초기화 & 브라우저 관리
###################################################################
def initialize_driver():
    """
    WebDriver 초기화 (undetected_chromedriver 사용)
    """
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    
    try:
        # 최신 버전 undetected_chromedriver에서는 executable_path 직접 지정 옵션
        if driver_path:
            driver = uc.Chrome(executable_path=driver_path, options=options)
        else:
            driver = uc.Chrome(options=options)
        return driver
    except Exception as e:
        logging.error(f"[initialize_driver] 드라이버 초기화 실패: {e}")
        return None

def handle_new_windows(driver, main_window=None):
    """
    새로 열린 창을 감지하고 닫는 함수
    """
    try:
        if not main_window:
            main_window = driver.current_window_handle
            
        # 모든 창 핸들 가져오기
        handles = driver.window_handles
        
        # 메인 창이 아닌 다른 창들 처리
        for handle in handles:
            if handle != main_window:
                try:
                    # 새 창으로 전환
                    driver.switch_to.window(handle)
                    logging.info(f"[새창감지] title='{driver.title}' => 닫기 시도")
                    
                    # 새 창 닫기
                    driver.close()
                    time.sleep(1)
                except Exception as ex:
                    logging.warning(f"[새창닫기실패] handle={handle}, error={ex}")
                    
        # 메인 창으로 돌아가기
        driver.switch_to.window(main_window)
        return True
        
    except Exception as ex:
        logging.error(f"[새창처리오류] {ex}")
        # 에러 발생시 메인 창으로 전환 시도
        try:
            driver.switch_to.window(main_window)
        except:
            pass
        return False

def check_and_close_new_windows(driver, store_code=None):
    """
    주기적으로 새 창을 체크하고 닫는 함수
    """
    main_window = driver.current_window_handle
    
    # 현재 창 개수 확인
    curr_count = len(driver.window_handles)
    
    # 창이 1개 이상이면 처리
    if curr_count > 1:
        store_info = f"[{store_code}] " if store_code else ""
        logging.info(f"{store_info}[창감지] {curr_count}개 창 발견")
        handle_new_windows(driver, main_window)
    
    return main_window  # 메인 창 핸들 반환

def close_popups(driver, timeout=5):
    """
    요기요 사이트의 팝업 닫기
    """
    popups = [
        {"name": "팝업1", "selector": "div[size='48'][color='primaryA']"},
        {"name": "알림창", "selector": "button.closeButton"},
        {"name": "모달창", "selector": "svg.FullScreenModal___StyledIcon-sc-7lyzl-8"}
    ]
    for p in popups:
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, p["selector"]))
            )
            el.click()
            logging.info(f"[팝업] {p['name']} 닫기 완료")
            time.sleep(1)
        except TimeoutException:
            logging.info(f"[팝업] {p['name']}은 발견되지 않음")
        except Exception as ex:
            logging.warning(f"[팝업오류] {ex}")
        time.sleep(1)

###################################################################
# 6) 로그인 및 페이지 이동
###################################################################
def login_to_yogiyo(driver, store_code, platform_id, platform_pw):
    """
    요기요 로그인 (platform_id, platform_pw 사용)
    """
    try:
        max_attempts = 3
        for attempt in range(1, max_attempts+1):
            logging.info(f"[로그인] 시도 {attempt}/{max_attempts}, store_code={store_code}")
            
            driver.get("https://ceo.yogiyo.co.kr/login/")
            time.sleep(3)
            
            # ID/PW 입력
            id_el = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            pw_el = driver.find_element(By.NAME, "password")
            id_el.clear()
            id_el.send_keys(platform_id)
            pw_el.clear()
            pw_el.send_keys(platform_pw)
            pw_el.send_keys(Keys.RETURN)
            logging.info(f"[로그인] ID/PW 입력 후 로그인버튼 클릭")
            time.sleep(5)
            
            # 에러 메시지 확인
            error_msgs = driver.find_elements(By.XPATH, "//p[contains(@class, 'error-msg')]")
            if error_msgs:
                for msg_el in error_msgs:
                    errtxt = msg_el.text.strip()
                    if errtxt:
                        logging.warning(f"[로그인] 에러 메시지: {errtxt}")
                if attempt == max_attempts:
                    take_screenshot(driver, store_code, "로그인실패")
                    save_error_log_to_supabase(
                        category="오류",
                        store_code=store_code,
                        error_type="로그인 실패",
                        error_message=f"최대 시도 횟수 초과 ({max_attempts}회)",
                        stack_trace=""
                    )
                    return False
                time.sleep(3)
                continue
            
            # 팝업 닫기
            close_popups(driver, 5)
            logging.info(f"[로그인성공] store_code={store_code}")
            return True
            
    except Exception as ex:
        logging.error(f"[로그인실패] store_code={store_code}, {ex}")
        take_screenshot(driver, store_code, "로그인실패")
        save_error_log_to_supabase(
            category="오류",
            store_code=store_code,
            error_type="로그인 실패",
            error_message=str(ex),
            stack_trace=traceback.format_exc()
        )
        return False

def select_store(driver, store_code, platform_code):
    """
    요기요 드롭다운에서 platform_code 에 해당하는 매장을 찾는다
    """
    try:
        dd_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.StoreSelector__DropdownButton-sc-1rowjsb-11"))
        )
        dd_btn.click()
        logging.info(f"[가게선택] 드롭다운 버튼 클릭 => store_code={store_code}")
        time.sleep(2)

        items = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "li.List__Vendor-sc-2ocjy3-7"))
        )
        matched = False
        for it in items:
            try:
                code_el = it.find_element(By.CSS_SELECTOR, "span.List__VendorID-sc-2ocjy3-1")
                splitted = code_el.text.split(". ")
                actual_code = splitted[1].strip() if len(splitted) > 1 else ""
                if actual_code == str(platform_code):
                    clk = it.find_element(By.CSS_SELECTOR, ".List__VendorLeftLayoutContent-sc-2ocjy3-5")
                    driver.execute_script("arguments[0].click();", clk)
                    time.sleep(2)
                    matched = True
                    logging.info(f"[가게선택] store_code={store_code}, platform_code={platform_code} => 선택완료")
                    break
            except:
                continue
        return matched
    except Exception as ex:
        logging.error(f"[select_store] store_code={store_code} => {ex}")
        return False

def navigate_to_reviews(driver, store_code, platform_code):
    """
    리뷰 페이지로 이동하고 미답변 탭 선택
    """
    try:
        driver.get("https://ceo.yogiyo.co.kr/reviews/")
        time.sleep(3)
        
        # 모달 닫기 시도
        try:
            modal_close = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "svg.FullScreenModal___StyledIcon-sc-7lyzl-8"))
            )
            modal_close.click()
            logging.info("[모달창] 닫기 완료")
        except TimeoutException:
            logging.info("[모달창] 없음")

        # 매장 선택
        ok = select_store(driver, store_code, platform_code)
        if not ok:
            raise Exception(f"platform_code({platform_code}) 불일치 또는 선택 실패")

        # 미답변 탭
        tab_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//li[contains(text(),'미답변')]"))
        )
        tab_btn.click()
        time.sleep(3)
        logging.info(f"[리뷰이동] store_code={store_code}, 미답변 탭 진입")
        return True
    except Exception as ex:
        logging.error(f"[navigate_to_reviews] store_code={store_code}, {ex}")
        take_screenshot(driver, store_code, "리뷰페이지이동실패")
        save_error_log_to_supabase(
            category="오류",
            store_code=store_code,
            error_type="리뷰 페이지 이동 실패",
            error_message=str(ex),
            stack_trace=traceback.format_exc()
        )
        return False

###################################################################
# 7) 리뷰 크롤링 및 데이터 가져오기
###################################################################
def scroll_to_bottom(driver):
    """무한 스크롤로 전체 리뷰 로드"""
    last_h = driver.execute_script("return document.body.scrollHeight")
    scroll_count = 0
    max_scrolls = 10  # 최대 스크롤 횟수 제한
    
    while scroll_count < max_scrolls:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_h = driver.execute_script("return document.body.scrollHeight")
        if new_h == last_h:
            break
        last_h = new_h
        scroll_count += 1

# 3. crawl_review_data 함수 수정 - 날짜 추출 추가
def crawl_review_data(driver, store_code):
    """
    요기요 리뷰 페이지에서 리뷰 카드 정보 크롤링
    """
    try:
        scroll_to_bottom(driver)
        time.sleep(2)
        elements = driver.find_elements(By.CSS_SELECTOR, "div.ReviewItem__Container-sc-1oxgj67-0")
        logging.info(f"[크롤링] store_code={store_code}, 리뷰카드={len(elements)}개 발견")

        reviews=[]
        for element in elements:
            try:
                # 답글 버튼이 있는 리뷰만 필터링
                add_btn = element.find_elements(By.CSS_SELECTOR, "button.ReviewReply__AddReplyButton-sc-1536a88-9")
                if not add_btn:
                    continue
                    
                # 별점
                star_el = element.find_element(By.CSS_SELECTOR, "h6.cknzqP")
                star_val = float(star_el.text.strip())
                
                # 작성자명
                author_el = element.find_element(By.CSS_SELECTOR, "h6.Typography__StyledTypography-sc-r9ksfy-0")
                author = author_el.text.strip()
                
                # 리뷰 텍스트
                txt_el = element.find_element(By.CSS_SELECTOR, "p.ReviewItem__CommentTypography-sc-1oxgj67-3")
                review_text = txt_el.text.strip()
                
                # 메뉴 정보 추출 시도 (있을 경우만)
                order_menu = ""
                try:
                    menu_els = element.find_elements(By.CSS_SELECTOR, ".ReviewMenus-module__menuName")
                    if menu_els:
                        order_menu = ", ".join([m.text.strip() for m in menu_els if m.text.strip()])
                except:
                    pass
                    
                # 배달 평가 추출 시도 (있을 경우만)
                delivery_review = ""
                try:
                    del_els = element.find_elements(By.CSS_SELECTOR, ".Badge_b_9yfm_19agxism")
                    if del_els:
                        delivery_review = ", ".join([d.text.strip() for d in del_els if d.text.strip()])
                except:
                    pass
                
                # 리뷰 날짜 추출 (새로 추가)
                review_date = extract_date_from_html(element)

                reviews.append({
                    "author": author,
                    "star": star_val,
                    "review_text": review_text,
                    "review_date": review_date,  # 추출한 날짜 추가
                    "order_menu": order_menu,
                    "delivery_review": delivery_review,
                    "element": element
                })
            except Exception as ex:
                logging.warning(f"[crawl_review_data] store_code={store_code}, 리뷰카드 처리 오류: {ex}")
                continue
                
        logging.info(f"[크롤링완료] store_code={store_code}, 최종={len(reviews)}개")
        return reviews
    except Exception as ex:
        logging.error(f"[crawl_review_data] store_code={store_code} 크롤링 실패: {ex}")
        take_screenshot(driver, store_code, "크롤링실패")
        save_error_log_to_supabase(
            category="오류",
            store_code=store_code,
            error_type="리뷰로드_실패",
            error_message=str(ex),
            stack_trace=traceback.format_exc()
        )
        return []

def get_shop_info(store_code, platform_code=None):
    """
    store_code로 매장 정보 가져오기
    platform_code가 제공된 경우 해당 매장으로 명확히 구분
    """
    try:
        # 기본 쿼리 생성
        query = supabase.table("platform_reply_rules") \
                       .select("*") \
                       .eq("store_code", store_code) \
                       .eq("platform", "요기요")
        
        # platform_code가 제공된 경우 추가 필터링
        if platform_code:
            query = query.eq("platform_code", str(platform_code))
            
        # 쿼리 실행
        response = query.execute()
        
        if response.data:
            shop_info = response.data[0]
            
            # 중요 필드에 기본값 설정
            greeting_start = shop_info.get("greeting_start")
            if not greeting_start:
                greeting_start = "안녕하세요!"
                
            greeting_end = shop_info.get("greeting_end")
            if not greeting_end:
                greeting_end = "감사합니다."
            
            # 로깅 - 매장 정보 확인용
            logging.info(f"[get_shop_info] store_code={store_code}, platform_code={platform_code}, store_name={shop_info.get('store_name', '')}")
            logging.info(f"[get_shop_info] greeting_start='{greeting_start}', greeting_end='{greeting_end}'")
            
            return {
                "store_name": shop_info.get("store_name", ""),
                "max_length": shop_info.get("max_lenth", 350),  # 필드명 주의: DB는 max_lenth
                "greeting_start": greeting_start,
                "greeting_end": greeting_end,
                "role": shop_info.get("role", "친절한 매장 직원"),
                "tone": shop_info.get("tone", "정중하고 친절한"),
                "prohibited_words": shop_info.get("prohibit_words", []),  # 필드명 주의: DB는 prohibit_words
                "rating_5_reply": shop_info.get("rating_5_reply", True),
                "rating_4_reply": shop_info.get("rating_4_reply", True),
                "rating_3_reply": shop_info.get("rating_3_reply", True),
                "rating_2_reply": shop_info.get("rating_2_reply", True),
                "rating_1_reply": shop_info.get("rating_1_reply", True),
                "store_type": shop_info.get("store_type", "delivery_only"),
                "platform_code": shop_info.get("platform_code", "")  # 저장해두면 유용
            }
        else:
            logging.warning(f"[get_shop_info] store_code={store_code}, platform_code={platform_code} 정보 없음")
            # 기본값 반환
            return {
                "store_name": "",
                "max_length": 350,
                "greeting_start": "안녕하세요!",
                "greeting_end": "감사합니다.",
                "role": "친절한 매장 직원",
                "tone": "정중하고 친절한",
                "prohibited_words": [],
                "rating_5_reply": True,
                "rating_4_reply": True,
                "rating_3_reply": True,
                "rating_2_reply": True,
                "rating_1_reply": True,
                "store_type": "delivery_only",
                "platform_code": platform_code if platform_code else ""
            }
    except Exception as e:
        logging.error(f"[get_shop_info] 오류: {e}")
        traceback.print_exc()  # 상세 오류 스택 출력
        # 오류 발생 시에도 기본값 반환
        return {
            "store_name": "",
            "max_length": 350,
            "greeting_start": "안녕하세요!",
            "greeting_end": "감사합니다.",
            "role": "친절한 매장 직원",
            "tone": "정중하고 친절한",
            "prohibited_words": [],
            "rating_5_reply": True,
            "rating_4_reply": True,
            "rating_3_reply": True,
            "rating_2_reply": True,
            "rating_1_reply": True,
            "store_type": "delivery_only",
            "platform_code": platform_code if platform_code else ""
        }
    
###################################################################
# 8) 리뷰 저장 관련 함수
###################################################################
def insert_review_to_supabase(
    store_code: str,
    store_name: str,
    platform_code: str,
    reviewer: str,
    star_rating: int,
    review_text: str,
    ai_reply: str,
    category: str = "",
    reason: str = "",
    boss_reply_needed: bool = False,
    review_date: str = None,  # 날짜 파라미터
    order_menu: str = "",
    delivery_review: str = "",
    response_status: str = None  # 새로 추가된 파라미터
):
    """
    리뷰 데이터를 reviews 테이블에 저장하거나 업데이트
    """
    try:
        # 리뷰 ID 생성 (해시)
        review_id = generate_review_hash(store_code, reviewer, review_text)
        
        # 현재 날짜
        if not review_date:
            review_date = datetime.now().strftime("%Y-%m-%d")
        
        # response_status 결정 로직
        if response_status is None:
            if boss_reply_needed:
                response_status = "사장님 확인필요"
            elif ai_reply:  # AI 답변이 있으면
                response_status = "답변완료"
            else:
                response_status = "답변대기"  # 기본값 변경
        
        # 데이터 준비
        data = {
            "store_code": store_code,
            "platform": "요기요",
            "platform_code": str(platform_code),
            "review_name": reviewer,
            "rating": star_rating,
            "review_content": review_text,
            "ordered_menu": order_menu,
            "delivery_review": delivery_review,
            "ai_response": ai_reply,
            "store_name": store_name,
            "response_status": response_status,
            "review_date": review_date,
            "review_category": category,
            "review_reason": reason,
            "boss_reply_needed": boss_reply_needed,
            "review_id": review_id,
            "updated_at": datetime.now().isoformat()
        }
        
        # 답변 완료 상태인 경우에만 response_at 설정
        if response_status == "답변완료":
            data["response_at"] = datetime.now().isoformat()
        
        # 기존 레코드 확인
        existing = supabase.table("reviews").select("*").eq("review_id", review_id).execute()
        
        if existing.data:
            # 기존 레코드가 있으면 업데이트
            record_id = existing.data[0]['id']
            record = existing.data[0]
            
            # retry_count 증가
            current_retry = record.get('retry_count', 0)
            data['retry_count'] = current_retry + 1
            
            # 기존 review_date 유지
            if 'review_date' in record and record['review_date']:
                data['review_date'] = record['review_date']
            
            resp = supabase.table("reviews").update(data).eq("id", record_id).execute()
            logging.info(f"[insert_review_to_supabase] 리뷰 업데이트 완료: id={record_id}, hash={review_id[:8]}...")
        else:
            # 새 레코드 삽입
            data['retry_count'] = 0  # 신규는 0부터 시작
            resp = supabase.table("reviews").insert(data).execute()
            logging.info(f"[insert_review_to_supabase] 새 리뷰 저장 완료: hash={review_id[:8]}...")
        
        # 세션 내 처리 완료 리뷰에 추가
        processed_reviews_in_session.add(review_id)
        return True
    except Exception as ex:
        logging.error(f"[insert_review_to_supabase] 오류: {ex}")
        return False

###################################################################
# 9) AI 리뷰 분석 관련 함수
###################################################################
def analyze_restaurant_review(review_text: str, rating: int, order_menu: str = "", delivery_review: str = "") -> dict:
    """
    음식점 리뷰를 더 세부적으로 분석하여 AI 답변 가능 여부와 상세 정보를 반환
    """
    try:
        # 1. 기본 정보 로깅
        logging.info("\n" + "="*50)
        logging.info("[분석 시작] 리뷰 정보")
        logging.info(f"별점: {rating}점")
        logging.info(f"리뷰: {review_text}")
        logging.info(f"주문메뉴: {order_menu}")
        logging.info(f"배달리뷰: {delivery_review}")
        logging.info("="*50)

        if not openai.api_key:
            raise ValueError("OpenAI API 키가 설정되지 않음")

        # 2. 텍스트가 없는 경우 별점 기반 처리
        if not review_text or review_text.strip() == "":
            return _handle_rating_only_review(rating)

        # 3. GPT 프롬프트 개선
        system_prompt = """
음식점 리뷰 분석 전문가로서 다음 기준으로 분석해주세요:

1. 감성 분석:
- 정확한 긍정/부정 키워드 파악
- 주문메뉴에 대한 만족도
- 배달 서비스 만족도
- 감정 강도 측정 (0~1)

2. 리뷰 카테고리 분류:
[음식]
- 맛과 품질 평가
- 조리 상태
- 음식 온도
- 양과 가격 대비 만족도

[서비스]
- 배달 시간 준수
- 포장 상태
- 직원 응대
- 정확한 주문 이행

[위생/안전]
- 음식 위생 상태
- 이물질 발견
- 신선도 문제
- 알레르기 관련

3. 심각도 판단:
- 위생 문제 가능성
- 법적 문제 가능성
- 이미지 손상 위험
- 재발 방지 필요성

4. AI 답변 가능성 평가:
- 일반적 피드백 vs 심각한 불만
- 구체적 조치 필요성
- 매장 정책 관련성
- 법적 책임 여부

다음 형식의 JSON으로만 응답하세요:
{
    "ai_reply": true/false,
    "sentiment_score": 0.0~1.0,
    "category": "메인카테고리",
    "sub_category": "세부카테고리",
    "keywords": ["주요", "키워드", "목록"],
    "severity": "LOW/MEDIUM/HIGH",
    "reason": "판단근거",
    "action_needed": ["필요한", "조치", "사항"]
}"""

        user_prompt = f"""
별점: {rating}점
주문메뉴: {order_menu}
배달리뷰: {delivery_review}
리뷰내용: {review_text}
"""
        # 4. GPT 분석 요청
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=500
        )

        result = json.loads(response.choices[0].message.content)
        
        # 5. 결과 보정 및 검증
        result = _adjust_analysis_result(result, rating)
        # 특별 케이스 처리 추가
        result = _override_analysis_for_special_cases(review_text, rating, result)
        result = _validate_analysis_result(result)
        
        # 6. 상세 로깅
        logging.info("\n[분석 결과]")
        for key, value in result.items():
            logging.info(f"- {key}: {value}")
        
        return result
    
    except Exception as e:
        logging.error(f"[분석 실패] 오류 발생: {e}")
        return _create_error_result("ERROR", str(e))

def _handle_rating_only_review(rating: int) -> dict:
    """별점만 있는 리뷰 처리"""
    base_result = {
        "keywords": ["별점"],
        "sub_category": "RATING_ONLY",
        "action_needed": []
    }
    
    if rating >= 4:
        return {
            **base_result,
            "ai_reply": True,  # 긍정적 리뷰는 AI가 답변
            "sentiment_score": 0.8,
            "category": "POSITIVE",
            "severity": "LOW",
            "reason": f"긍정적인 {rating}점 리뷰",
            "action_needed": []  # 긍정 리뷰는 조치 불필요
        }
    elif rating <= 2:
        return {
            **base_result,
            "ai_reply": False,
            "sentiment_score": 0.2,
            "category": "NEGATIVE",
            "severity": "HIGH",
            "reason": f"부정적인 {rating}점 리뷰",
            "action_needed": ["사장님 확인 필요"]
        }
    else:
        return {
            **base_result,
            "ai_reply": True,  # 중립적 리뷰도 AI가 답변
            "sentiment_score": 0.5,
            "category": "NEUTRAL",
            "severity": "MEDIUM",
            "reason": f"중립적인 {rating}점 리뷰"
        }

def _adjust_analysis_result(result: dict, rating: int) -> dict:
    """분석 결과를 보정"""
    # 1. 감성 점수 보정
    if rating >= 4 and result['sentiment_score'] < 0.3:
        result['sentiment_score'] = max(0.4, result['sentiment_score'])
    elif rating <= 2 and result['sentiment_score'] > 0.7:
        result['sentiment_score'] = min(0.6, result['sentiment_score'])
    
    # 2. 심각도 기반 AI 답변 여부 결정
    if result['severity'] == 'HIGH':
        result['ai_reply'] = False
        if 'action_needed' not in result:
            result['action_needed'] = []
        if "사장님 확인 필요" not in result['action_needed']:
            result['action_needed'].append('사장님 확인 필요')
    
    # 3. 별점 기반 최종 보정
    if rating >= 4:
        # 4점 이상은 거의 항상 AI 답변 가능
        if result.get('sentiment_score', 0) > 0.4:
            result['ai_reply'] = True
    elif rating <= 1:
        # 1점은 대부분 사장님 확인 필요
        if result.get('sentiment_score', 1) < 0.4:
            result['ai_reply'] = False
            result['severity'] = 'HIGH'
            if "사장님 확인 필요" not in result.get('action_needed', []):
                if 'action_needed' not in result:
                    result['action_needed'] = []
                result['action_needed'].append('사장님 확인 필요')
    
    return result

def _override_analysis_for_special_cases(review_text: str, rating: int, result: dict) -> dict:
    """특별한 케이스의 리뷰를 위한 분석 결과 오버라이드"""
    
    # 1. 이모지만 있는 경우 (예: 👍👍👍👍👍👍👍👍👍👍)
    emoji_pattern = re.compile(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002702-\U000027B0]')
    
    # 이모지만 있거나 대부분인 리뷰
    if review_text:
        emoji_count = len(emoji_pattern.findall(review_text))
        if emoji_count > 0 and (len(review_text.strip()) <= 5 or emoji_count / len(review_text.strip()) > 0.5):
            # 긍정적 이모지가 주로 쓰였고 별점이 높으면
            if rating >= 4:
                return {
                    'ai_reply': True,
                    'sentiment_score': 0.9,
                    'category': '긍정',
                    'sub_category': '이모지 피드백',
                    'keywords': ['이모지', '긍정', '만족'],
                    'severity': 'LOW',
                    'reason': '긍정적인 이모지 리뷰',
                    'action_needed': []
                }
    
    # 2. 내용이 거의 없는 단순 긍정 리뷰
    minimal_pattern = re.compile(r'^(좋아요|맛있어요|잘 먹었어요|맛있었어요|굿|감사합니다|최고|짱|JMT|굳|Good|nice)[\s!]*$', re.IGNORECASE)
    if review_text and minimal_pattern.match(review_text.strip()) and rating >= 4:
        return {
            'ai_reply': True,
            'sentiment_score': 0.85,
            'category': '긍정',
            'sub_category': '단순 긍정',
            'keywords': ['긍정', '만족', '간결'],
            'severity': 'LOW',
            'reason': '짧은 긍정 리뷰',
            'action_needed': []
        }
    
    # 3. 이물질, 위생 관련 단어가 있는 경우
    if review_text:
        critical_issues = ['이물질', '머리카락', '벌레', '곰팡이', '상했', '토했', '체했', '식중독', 
                          '비위생', '불결', '불만', '환불', '고객센터', '신고', '고발']
        
        for issue in critical_issues:
            if issue in review_text:
                return {
                    'ai_reply': False,
                    'sentiment_score': 0.1,
                    'category': '위생',
                    'sub_category': '심각한 불만',
                    'keywords': ['위생', '이물질', '불만'],
                    'severity': 'HIGH',
                    'reason': f'위생 관련 심각한 불만 포함: {issue}',
                    'action_needed': ['사장님 확인 필요', '신속 대응 필요']
                }
    
    # 기존 결과 유지
    return result

def _validate_analysis_result(result: dict) -> dict:
    """분석 결과의 형식 검증"""
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
            try:
                result[field] = field_type(result[field])
            except:
                result[field] = field_type()
    
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

###################################################################
# 10) AI 답변 생성 관련 함수
###################################################################
def clean_ai_reply(ai_reply):
    """
    AI 답변 전처리: 유니코드 정규화 및 줄바꿈 처리
    """
    ai_reply = unicodedata.normalize('NFC', ai_reply)
    ai_reply = ai_reply.replace('\\n', '\n')
    cleaned = ''.join(c for c in ai_reply if ord(c) <= 0xFFFF and (c.isprintable() or c == '\n'))
    return cleaned

def validate_reply_content(ai_reply: str, shop_info: dict) -> tuple[bool, str]:
    """AI 답변의 내용을 검증"""
    
    # 1. 기본 검증
    total_chars = len(ai_reply)
    if total_chars == 0:
        return False, "빈 답변"
        
    # 2. 한글/기본 문장 부호/영문 허용 (유연하게)
    valid_pattern = re.compile(r'[가-힣A-Za-z\s.,!?0-9%♡~()]')
    valid_chars = len([c for c in ai_reply if valid_pattern.match(c) or ord(c) > 0x1F000])
    
    invalid_ratio = (total_chars - valid_chars) / total_chars
    if invalid_ratio > 0.2:  # 20%까지 허용 (이모지 등을 위해)
        return False, "허용되지 않는 문자 과다 사용"

    # 3. 금지어 체크
    prohibited = shop_info.get('prohibited_words', [])
    if isinstance(prohibited, str):
        try:
            prohibited = eval(prohibited)
        except:
            prohibited = []
            if ',' in shop_info.get('prohibited_words', ''):
                prohibited = [word.strip() for word in shop_info.get('prohibited_words', '').split(',')]
    
    # 실제 금지어만 필터링 (빈 문자열 등 제외)
    prohibited = [word for word in prohibited if word and len(word.strip()) > 1]
    
    # 매장 유형에 따른 금지어 처리
    store_type = shop_info.get("store_type", "")
    if store_type == "delivery_only":
        # 배달 전문점이면 '방문', '홀' 등 관련 단어 체크
        location_words = ['방문', '홀', '매장 식사', '매장에서']
        for word in location_words:
            if word in ai_reply:
                return False, f"배달전문점 금지어 '{word}' 사용"
    
    # 기존 금지어 체크
    for word in prohibited:
        if len(word.strip()) > 1 and word.strip() in ai_reply:
            return False, f"금지어 '{word}' 사용"
    
    # 5. 길이 제한
    max_length = shop_info.get('max_length')
    if len(ai_reply) > max_length:
        return False, f"길이 초과 ({len(ai_reply)} > {max_length})"
    
    return True, ""

def generate_ai_reply(review: dict, shop_info: dict) -> str:
    """
    AI 답변 생성 함수
    """
    try:
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            logging.error("OpenAI API 키가 설정되지 않았습니다.")
            return None
            
        # 디버깅용 로그 추가
        store_name = shop_info.get("store_name", "")
        greeting_end = shop_info.get("greeting_end", "감사합니다.")
        
        logging.info(f"[generate_ai_reply] 매장명: {store_name}")
        logging.info(f"[generate_ai_reply] 맺음말: '{greeting_end}'")

        # 매장 유형
        store_type = shop_info.get("store_type", "delivery_only")
        
        # 시작 인사 예시 제공
        greeting_start = shop_info.get("greeting_start", "안녕하세요!")
        greeting_examples = f"'{greeting_start}', '{greeting_start} 고객님,', '{greeting_start} 고객님!'"

        # 배달 전문 / 홀+배달에 따른 추가 지시
        if store_type == "delivery_only":
            type_instruction = (
                "매장은 배달전문 업체입니다. 배달 전문에 맞게 응답해주세요."
                " '홀', '방문', '매장 식사' 표현은 절대 사용하지 마세요. "
                "'주문'이나 '배달'이라는 표현만 사용해주세요."
            )
        else:  # "dine_in_delivery" 등으로 가정
            type_instruction = (
                "매장은 홀+배달 운영이 가능합니다. 리뷰에서 질문시에만 안내해주세요. "
            )

        # 주문 메뉴 언급
        order_menu_prompt = ""
        if review.get('order_menu'):
            order_menu_prompt = f"주문 메뉴: {review.get('order_menu')}\n- 메뉴가 있으면 반드시 답변에서 언급해주세요."

        prompt_system = f"""
당신은 '{store_name}' 리뷰 답변 전문가입니다.

## 필수 규칙:
1. 시작 인사는 반드시 다음 중 하나로 시작하세요: {greeting_examples}
2. 배달 어플에 남긴 리뷰입니다.
3. 끝 인사는 반드시 '{greeting_end}'로 끝내야 합니다.
4. 답변은 최대 {shop_info.get('max_length', 350)}자 이내로 작성하세요.

## 매장 정보:
- 매장 유형: {store_type}
- {type_instruction}
- 답변 역할: {shop_info.get('role', '친절한 매장 직원')}
- 답변 톤: {shop_info.get('tone', '정중하고 친절한')}

## 답변 작성 지침:
- 고객의 구체적인 피드백에 직접 응답하세요.
- 부정적 리뷰에는 변명하지 말고 개선 의지를 표현하세요.
- 긍정적 리뷰에는 구체적으로 감사를 표현하세요.
- {order_menu_prompt}

## 추가 지침:
- 금지어: {shop_info.get('prohibited_words', [])}
- 다시 강조: 맺음말은 반드시 '{greeting_end}'로 끝내야 합니다.
"""

        prompt_user = (
            f"리뷰 작성자: {review['author']}, 별점: {review['star']}점\n"
            f"리뷰내용: {review['review_text']}\n"
            f"메뉴: {review.get('order_menu', '')}\n"
            f"배달평가: {review.get('delivery_review', '')}\n\n"
            f"위 규칙을 철저히 지키면서 적절한 답변을 작성해주세요. 특히 맺음말은 '{greeting_end}'로 해주세요."
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": prompt_user},
            ],
            max_tokens=500
        )
        ai_reply = response.choices[0].message.content
        ai_reply = clean_ai_reply(ai_reply)
        
        # 맺음말 검증 및 강제 추가
        if greeting_end not in ai_reply:
            logging.warning(f"[generate_ai_reply] 맺음말('{greeting_end}') 누락, 강제 추가")
            # 줄바꿈 추가 후 맺음말 강제 추가
            if not ai_reply.endswith('\n'):
                ai_reply += '\n'
            ai_reply += greeting_end
        
        logging.info(f"[generate_ai_reply] 생성 완료, 길이: {len(ai_reply)}자")
        return ai_reply

    except Exception as e:
        logging.error(f"AI 답변 생성 중 오류 발생: {e}")
        traceback.print_exc()  # 상세 스택 트레이스 출력
        return None

def generate_ai_reply_with_retry(review: dict, shop_info: dict, max_attempts: int = 3) -> str:
    """검증을 통과할 때까지 최대 3회 재시도하는 답변 생성 함수"""
    
    best_reply = None
    best_score = 0
    
    for attempt in range(max_attempts):
            logging.info(f"\n[AI 답변 생성] 시도 {attempt + 1}/{max_attempts}")
            
            # 1. AI 답변 생성
            ai_reply = generate_ai_reply(review, shop_info)
            if not ai_reply:
                logging.warning("[AI 답변 생성] 실패")
                continue
                
            # 2. 형식 검증
            is_valid, reason = validate_reply_content(ai_reply, shop_info)
            if not is_valid:
                logging.warning(f"[AI 답변 검증] 실패: {reason}")
                continue
                
            # 3. 품질 점수 평가
            score, details = score_reply(ai_reply, review, shop_info)
            logging.info(f"[AI 답변 평가] 점수: {score}")
            
            # 더 나은 답변 저장
            if score > best_score:
                best_score = score
                best_reply = ai_reply
                
            # 목표 점수 달성시 즉시 반환
            if score >= 80:
                logging.info("[AI 답변 선택] 목표 점수 달성")
                return ai_reply
                
        # 모든 시도 후에도 80점을 넘지 못하면 최고 점수 답변 반환
    if best_reply:
        logging.info(f"[AI 답변 선택] 최고 점수({best_score}) 답변 선택")
        return best_reply
            
    logging.error("[AI 답변 생성] 모든 시도 실패")
    return None

def score_reply(ai_reply: str, review: dict, shop_info: dict, threshold: int = 80) -> tuple[int, dict]:
    """
    AI 답변의 품질을 세부적으로 평가합니다.
    
    Returns:
        tuple[총점, 세부점수_딕셔너리]
    """
    try:
        if not openai.api_key:
            logging.warning("[score_reply] OpenAI API 키 없음")
            return 85, {}  # API 키가 없으면 합격 처리

        system_prompt = """
당신은 전문적인 리뷰 답변 평가자입니다. 다음 기준으로 평가하고 정확히 아래 JSON 형식으로만 응답하세요:

1. 맥락 이해도 (30점)
- 리뷰 내용 정확한 파악: 10점
- 고객 감정/니즈 이해: 10점
- 적절한 대응 방향: 10점

2. 전문성 (20점)
- 음식/서비스 관련 전문성: 10점
- 구체적인 설명/해결책: 10점

3. 형식 완성도 (20점)
- 인사말/맺음말 적절성: 5점
- 단락 구성의 논리성: 5점
- 글자 수 제한 준수: 5점
- 적절한 단락 구분: 5점

4. 어조와 태도 (15점)
- 공손하고 친절한 태도: 5점
- 적절한 경어 사용: 5점
- 진정성 있는 표현: 5점

5. 문장 품질 (15점)
- 맞춤법/문법 정확성: 5점
- 자연스러운 문장 흐름: 5점
- 간결하고 명확한 표현: 5점

각 항목의 점수와 총점을 다음 JSON 형식으로 반환하세요:
{
    "total_score": 점수,
    "context_score": {"리뷰이해": 점수, "감정이해": 점수, "대응방향": 점수},
    "expertise_score": {"전문성": 점수, "구체성": 점수},
    "format_score": {"인사말": 점수, "구조": 점수, "길이": 점수, "단락": 점수},
    "tone_score": {"공손함": 점수, "경어사용": 점수, "진정성": 점수},
    "quality_score": {"맞춤법": 점수, "자연스러움": 점수, "간결성": 점수},
    "improvement_needed": ["개선필요사항1", "개선필요사항2"]
}"""

        user_prompt = f"""
원본 리뷰 정보:
작성자: {review['author']}
별점: {review['star']}점
리뷰내용: {review['review_text']}
{f"주문메뉴: {review.get('order_menu', '')}" if review.get('order_menu') else ""}
{f"배달평가: {review.get('delivery_review', '')}" if review.get('delivery_review') else ""}

AI 답글:
{ai_reply}

매장 정보:
- 시작 인사: {shop_info['greeting_start']}
- 끝 인사: {shop_info['greeting_end']}
- 최대 길이: {shop_info['max_length']}자
- 매장 역할: {shop_info['role']}
- 답변 톤: {shop_info['tone']}

각 평가 항목의 점수를 매겨주세요."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=500
        )

        try:
            content = response.choices[0].message.content
            
            # JSON 형식만 추출
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group(0))
            else:
                logging.warning(f"[score_reply] JSON 형식을 찾을 수 없음: {content[:100]}...")
                return 85, {}  # 기본값 반환
                
            # 총점 계산
            total_score = result.get("total_score", 0)
            if total_score == 0:
                # 항목별 점수 합산
                category_scores = [
                    sum(result.get('context_score', {}).values()),
                    sum(result.get('expertise_score', {}).values()),
                    sum(result.get('format_score', {}).values()),
                    sum(result.get('tone_score', {}).values()),
                    sum(result.get('quality_score', {}).values())
                ]
                total_score = sum(category_scores)
                result["total_score"] = total_score
            
            # 세부 점수 로깅
            logging.info("\n[답변 품질 평가]")
            logging.info(f"총점: {total_score}")
            
            for category, scores in {
                "맥락 이해도": result.get('context_score', {}),
                "전문성": result.get('expertise_score', {}),
                "형식 완성도": result.get('format_score', {}),
                "어조와 태도": result.get('tone_score', {}),
                "문장 품질": result.get('quality_score', {})
            }.items():
                logging.info(f"\n{category}:")
                for name, score in scores.items():
                    logging.info(f"- {name}: {score}점")
            
            # 개선 필요 사항
            if result.get('improvement_needed'):
                logging.info("\n개선 필요 사항:")
                for item in result['improvement_needed']:
                    logging.info(f"- {item}")
            
            # 재시도 트리거 조건 확인
            should_retry = _check_retry_conditions(result)
            if should_retry:
                logging.info("\n[재시도 권장]")
                
            return total_score, result
            
        except json.JSONDecodeError as e:
            logging.error(f"[score_reply] JSON 파싱 오류: {e}")
            return 80, {}  # 오류 시 기본값
            
    except Exception as e:
        logging.error(f"[score_reply] 평가 중 오류: {e}")
        return 80, {}  # 오류 시 기본값

def _check_retry_conditions(scores: dict) -> bool:
    """재시도가 필요한지 확인"""
    
    # 1. 치명적인 문제 체크
    critical_issues = []
    
    if 'context_score' in scores and '리뷰이해' in scores['context_score']:
        critical_issues.append(scores['context_score']['리뷰이해'] < 5)  # 리뷰 내용 완전 오해
        
    if 'format_score' in scores and '인사말' in scores['format_score']:
        critical_issues.append(scores['format_score']['인사말'] < 2)  # 인사말 심각한 문제
        
    if 'tone_score' in scores and '공손함' in scores['tone_score']:
        critical_issues.append(scores['tone_score']['공손함'] < 2)  # 매우 불손한 태도
    
    if any(critical_issues):
        return True
    
    # 2. 낮은 점수 영역 체크
    low_scores = []
    
    if 'context_score' in scores:
        low_scores.append(sum(scores['context_score'].values()) < 15)  # 맥락 이해 부족
    
    if 'expertise_score' in scores:
        low_scores.append(sum(scores['expertise_score'].values()) < 10)  # 전문성 부족
    
    if 'format_score' in scores:
        low_scores.append(sum(scores['format_score'].values()) < 10)  # 형식 문제
    
    if 'tone_score' in scores:
        low_scores.append(sum(scores['tone_score'].values()) < 8)  # 태도 문제
    
    if 'quality_score' in scores:
        low_scores.append(sum(scores['quality_score'].values()) < 8)  # 품질 문제
    
    # 2개 이상 영역에서 낮은 점수면 재시도
    return sum([1 for x in low_scores if x]) >= 2

###################################################################
# 11) 댓글 등록 함수
###################################################################
def post_review_response(driver, store_code, rv, ai_reply):
    """
    요기요 댓글 등록
    """
    try:
        # 답변 위치 검증 - 답글이 올바른 리뷰에 작성되는지 확인
        element = rv['element']
        
        # 현재 선택된 리뷰 요소에서 작성자 정보 추출
        try:
            author_el = element.find_element(By.CSS_SELECTOR, "h6.Typography__StyledTypography-sc-r9ksfy-0")
            current_author = author_el.text.strip()
            
            # 현재 요소의 작성자가 rv에 저장된 작성자와 일치하는지 확인
            if current_author != rv['author']:
                error_msg = f"작성자 불일치: 예상={rv['author']}, 실제={current_author}"
                logging.error(f"[검증실패] store_code={store_code}, {error_msg}")
                take_screenshot(driver, store_code, "답변위치검증실패")
                save_error_log_to_supabase(
                    category="검증실패",
                    store_code=store_code,
                    error_type="리뷰-답변 위치 불일치",
                    error_message=error_msg,
                    stack_trace=""
                )
                return False
        except Exception as ve:
            logging.error(f"[검증오류] store_code={store_code}, {ve}")
            take_screenshot(driver, store_code, "작성자검증실패")
            save_error_log_to_supabase(
                category="검증실패",
                store_code=store_code,
                error_type="작성자검증실패",
                error_message=str(ve),
                stack_trace=traceback.format_exc()
            )
            return False
            
        # 혹시 열려있는 창이면 '취소' 버튼
        cancels = driver.find_elements(By.XPATH,"//button[span[contains(text(),'취소')]]")
        if cancels:
            driver.execute_script("arguments[0].click();", cancels[0])
            time.sleep(1)

        # 카드 요소에서 답글 등록 버튼 찾기
        add_btn = WebDriverWait(element, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.ReviewReply__AddReplyButton-sc-1536a88-9"))
        )
        add_btn.click()
        time.sleep(1)

        # 텍스트 영역 찾기
        textarea = WebDriverWait(element, 10).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "textarea.ReviewReply__CustomTextarea-sc-1536a88-4"))
        )
        textarea.clear()
        
        # 줄바꿈 처리 (행별로 입력)
        lines = ai_reply.split('\n')
        for i, line in enumerate(lines):
            textarea.send_keys(line)
            if i < len(lines) - 1:
                textarea.send_keys(Keys.SHIFT + Keys.ENTER)
        
        time.sleep(1)
        
        # 등록 버튼 찾아 클릭
        register_btn = WebDriverWait(element, 10).until(
            EC.element_to_be_clickable((By.XPATH, ".//button[span[text()='등록']]"))
        )
        register_btn.click()
        time.sleep(2)
        
        logging.info(f"[댓글등록] store_code={store_code}, 작성자={rv['author']}, 별점={rv['star']}")
        return True
    except Exception as ex:
        logging.error(f"[댓글등록 실패] store_code={store_code}, {ex}")
        take_screenshot(driver, store_code, "답글실패")
        save_error_log_to_supabase(
            category="오류",
            store_code=store_code,
            error_type="댓글 등록 실패",
            error_message=str(ex),
            stack_trace=traceback.format_exc()
        )
        return False
###################################################################
# 12) 리뷰 분석 및 처리 통합 함수
###################################################################
def process_review_with_analysis(driver, store_code, store_name, platform_code, rv, shop_info=None):
    """
    리뷰 분석 및 처리 통합 함수
    - 새 리뷰는 '답변대기'로 저장
    - 2일 경과된 '답변대기' 리뷰에 답변
    - 4일 경과된 '사장님 확인필요' 리뷰에 답변
    """
    author = rv['author']
    star_rating = int(rv['star']) if rv['star'] else 0
    review_text = rv['review_text']
    order_menu = rv.get('order_menu', '')
    delivery_review = rv.get('delivery_review', '')
    review_date = rv.get('review_date')  # HTML에서 추출한 날짜 사용
    
    # 리뷰 해시 생성
    review_id = generate_review_hash(store_code, author, review_text)
    
    # 매장 설정 가져오기 (전달받지 않았을 경우만)
    if shop_info is None:
        shop_info = get_shop_info(store_code)
    
    # 매장 정보 로깅 - 디버깅용
    logging.info(f"[process_review] store_code={store_code}, greeting_end='{shop_info.get('greeting_end')}'")
    
    # 별점 기반 답변 여부 확인
    rating_key = f"rating_{star_rating}_reply"
    if not shop_info.get(rating_key, True):
        logging.info(f"[process_review_with_analysis] 별점({star_rating}) 자동답글 제외")
        insert_review_to_supabase(
            store_code=store_code,
            store_name=store_name,
            platform_code=platform_code,
            reviewer=author,
            star_rating=star_rating,
            review_text=review_text,
            ai_reply="",
            category="별점 제외",
            reason=f"별점({star_rating})에 대한 자동 답변 비활성화됨",
            boss_reply_needed=True,
            review_date=review_date,
            order_menu=order_menu,
            delivery_review=delivery_review
        )
        return
    
    # 기존 리뷰 확인
    existing = supabase.table("reviews").select("*").eq("review_id", review_id).execute()
    existing_record = existing.data[0] if existing.data else None
    
    # 1. 이미 존재하는 리뷰 처리 로직
    if existing_record:
        status = existing_record.get('response_status', '')
        review_date_str = existing_record.get('review_date', '')
        
        if status == "답변대기":
            # '답변대기' 상태인 리뷰 처리
            if review_date_str:
                try:
                    review_date_obj = datetime.strptime(review_date_str, "%Y-%m-%d").date()
                    days_passed = (datetime.now().date() - review_date_obj).days
                    
                    if days_passed >= 2:
                        logging.info(f"[답변대기처리] {days_passed}일 경과 => AI 답변 시도")
                        
                        # 리뷰 분석
                        analysis_result = analyze_restaurant_review(review_text, star_rating, order_menu, delivery_review)
                        category = analysis_result.get('category', '')
                        reason = analysis_result.get('reason', '')
                        
                        if not analysis_result['ai_reply']:
                            # AI가 답변하지 않는 케이스 - 사장님 확인필요로 변경
                            logging.info(f"[사장님답변필요] 이유: {reason}")
                            insert_review_to_supabase(
                                store_code=store_code,
                                store_name=store_name,
                                platform_code=platform_code,
                                reviewer=author,
                                star_rating=star_rating,
                                review_text=review_text,
                                ai_reply="",
                                category=category,
                                reason=reason,
                                boss_reply_needed=True,
                                review_date=review_date_str,  # 기존 날짜 유지
                                order_menu=order_menu,
                                delivery_review=delivery_review
                            )
                            return
                        
                        # AI 답변 생성 시도
                        ai_reply = generate_ai_reply_with_retry(rv, shop_info)
                        
                        if ai_reply and post_review_response(driver, store_code, rv, ai_reply):
                            # 성공 시 답변완료로 상태 변경
                            insert_review_to_supabase(
                                store_code=store_code,
                                store_name=store_name,
                                platform_code=platform_code,
                                reviewer=author,
                                star_rating=star_rating,
                                review_text=review_text,
                                ai_reply=ai_reply,
                                category=category,
                                reason=reason,
                                boss_reply_needed=False,
                                review_date=review_date_str,  # 기존 날짜 유지
                                order_menu=order_menu,
                                delivery_review=delivery_review
                            )
                        else:
                            # 답변 생성 또는 등록 실패 - 사장님 확인필요로 변경
                            insert_review_to_supabase(
                                store_code=store_code,
                                store_name=store_name,
                                platform_code=platform_code,
                                reviewer=author,
                                star_rating=star_rating,
                                review_text=review_text,
                                ai_reply="",
                                category=category,
                                reason="AI 답변 생성 또는 등록 실패",
                                boss_reply_needed=True,
                                review_date=review_date_str,  # 기존 날짜 유지
                                order_menu=order_menu,
                                delivery_review=delivery_review
                            )
                        return
                except Exception as e:
                    logging.error(f"[날짜처리오류] {e}")
        
        elif status == "사장님 확인필요":
            # '사장님 확인필요' 상태인 리뷰 처리
            if review_date_str:
                try:
                    review_date_obj = datetime.strptime(review_date_str, "%Y-%m-%d").date()
                    days_passed = (datetime.now().date() - review_date_obj).days
                    
                    if days_passed >= 4:
                        logging.info(f"[사장님확인필요처리] {days_passed}일 경과 => AI 답변 시도")
                        
                        # AI 답변 생성 시도
                        ai_reply = generate_ai_reply_with_retry(rv, shop_info)
                        
                        if ai_reply and post_review_response(driver, store_code, rv, ai_reply):
                            # 성공 시 답변완료로 상태 변경
                            insert_review_to_supabase(
                                store_code=store_code,
                                store_name=store_name,
                                platform_code=platform_code,
                                reviewer=author,
                                star_rating=star_rating,
                                review_text=review_text,
                                ai_reply=ai_reply,
                                review_date=review_date_str,  # 기존 날짜 유지
                                boss_reply_needed=False,
                                order_menu=order_menu,
                                delivery_review=delivery_review
                            )
                        return
                except Exception as e:
                    logging.error(f"[날짜처리오류] {e}")
    
    # 2. 새 리뷰 처리 로직 (존재하지 않는 리뷰)
    # 리뷰 분석
    analysis_result = analyze_restaurant_review(review_text, star_rating, order_menu, delivery_review)
    category = analysis_result.get('category', '')
    reason = analysis_result.get('reason', '')
    
    # 분석 결과에 관계없이 모든 새 리뷰는 일단 '답변대기'로 저장
    if not analysis_result['ai_reply']:
        # AI가 답변할 수 없는 리뷰는 '사장님 확인필요'로 저장
        logging.info(f"[사장님답변필요] 이유: {reason}")
        insert_review_to_supabase(
            store_code=store_code,
            store_name=store_name,
            platform_code=platform_code,
            reviewer=author,
            star_rating=star_rating,
            review_text=review_text,
            ai_reply="",
            category=category,
            reason=reason,
            boss_reply_needed=True,  # 사장님 확인 필요
            review_date=review_date,
            order_menu=order_menu,
            delivery_review=delivery_review
        )
    else:
        # AI가 답변 가능한 리뷰는 '답변대기'로 저장
        logging.info(f"[답변대기저장] 리뷰 저장, 2일 후 처리 예정")
        insert_review_to_supabase(
            store_code=store_code,
            store_name=store_name,
            platform_code=platform_code,
            reviewer=author,
            star_rating=star_rating,
            review_text=review_text,
            ai_reply="",  # 아직 답변 생성하지 않음
            category=category,
            reason=reason + " (자동응답 대기중)",
            boss_reply_needed=False,
            response_status="답변대기",  # 상태를 '답변대기'로 설정
            review_date=review_date,
            order_menu=order_menu,
            delivery_review=delivery_review
        )
###################################################################
# 13) 매장 처리 로직
###################################################################
def process_yogiyo_store(driver, shop_info):
    """
    요기요 매장 처리 함수
    """
    store_code = shop_info["store_code"]
    store_name = shop_info["store_name"]
    platform_code = shop_info["platform_code"]
    platform_id = shop_info["platform_id"]
    platform_pw = shop_info["platform_pw"]

    logging.info(f"\n===== [매장 처리 시작] {store_name} (store_code={store_code}) =====")
    
    # 매장별 shop_info 복사 (참조 방지)
    current_shop_info = dict(shop_info)
    
    # 중요 필드 검증 및 로깅
    greeting_start = current_shop_info.get("greeting_start")
    if not greeting_start:
        current_shop_info["greeting_start"] = "안녕하세요!"
        
    greeting_end = current_shop_info.get("greeting_end")
    if not greeting_end:
        current_shop_info["greeting_end"] = "감사합니다."
    
    # 매장 정보 로깅
    logging.info(f"[매장정보] store_code={store_code}, store_name={store_name}")
    logging.info(f"[매장정보] greeting_start='{current_shop_info['greeting_start']}', greeting_end='{current_shop_info['greeting_end']}'")
    
    # 1) 로그인
    if not login_to_yogiyo(driver, store_code, platform_id, platform_pw):
        return
    check_and_close_new_windows(driver)

    # 2) 리뷰페이지 이동
    if not navigate_to_reviews(driver, store_code, platform_code):
        return
    check_and_close_new_windows(driver)

    # 3) 리뷰 크롤링
    rv_list = crawl_review_data(driver, store_code)
    if not rv_list:
        logging.info(f"[리뷰없음] store_code={store_code}")
        return
    check_and_close_new_windows(driver)
    
    # 이미 처리한 리뷰의 해시값을 저장하는 세트 (세션 내 중복 방지)
    processed_in_session = set()
    
    # 4) 리뷰 처리
    for idx, rv in enumerate(rv_list, 1):
        # 해시 생성
        review_hash = generate_review_hash(store_code, rv['author'], rv['review_text'])
        
        # 이미 이번 세션에서 처리했는지 확인
        if review_hash in processed_in_session:
            logging.info(f"[이미처리] 해시={review_hash[:8]}... 세션 내 중복 스킵")
            continue
        
        # 개선된 중복 체크 로직 적용
        if _check_duplicate_review(driver, store_code, review_hash, rv['author'], rv['review_text']):
            # True가 반환되면 이 리뷰는 스킵
            continue
            
        logging.info(f"[리뷰] {idx}/{len(rv_list)} => 작성자={rv['author']}, 별점={rv['star']}")
        
        # 중요: shop_info를 매개변수로 전달
        process_review_with_analysis(driver, store_code, store_name, platform_code, rv, current_shop_info)
        
        # 처리된 리뷰 해시 저장 (세션 내 중복 방지)
        processed_in_session.add(review_hash)
        
        # 처리 후 잠시 대기 (서버 부하 방지)
        time.sleep(1)

    # 5) 로그아웃
    try:
        driver.get("https://ceo.yogiyo.co.kr/my/")
        time.sleep(3)
        logout_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//div[contains(text(),'로그아웃')]"))
        )
        logout_btn.click()
        time.sleep(2)
        logging.info(f"[로그아웃] store_code={store_code}")
    except Exception as ex:
        logging.error(f"[로그아웃 실패] store_code={store_code}, {ex}")
        save_error_log_to_supabase(
            category="오류",
            store_code=store_code,
            error_type="LOGOUT_FAIL",
            error_message=str(ex),
            stack_trace=traceback.format_exc()
        )

    logging.info(f"===== [매장 처리 끝] {store_name} =====\n")
    
###################################################################
# 14) 메인 실행 함수
###################################################################
def run_automation():
    """
    UI에서 실행되는 메인 함수
    """
    global processed_reviews_in_session
    processed_reviews_in_session = set()  # 세션 초기화
    
    if not driver_path:
        messagebox.showerror("오류", "크롬드라이버 경로가 설정되지 않았습니다.")
        return

    # 1) Supabase에서 데이터 불러오기
    shop_rows = fetch_yogiyo_data()
    if not shop_rows:
        messagebox.showerror("오류","요기요용 데이터가 없습니다.")
        return

    # 실행 모드 확인
    mode = execution_mode.get()
    if mode == 'partial':
        text_value = range_entry.get().strip()
        if not text_value:
            messagebox.showerror("오류", "부분 실행 모드에서 범위를 입력해주세요. 예: STORE00001, STORE00005")
            return
        
        try:
            start_code, end_code = [x.strip() for x in text_value.split(',')]
            # store_code가 start_code~end_code 범위에 있는 것만 필터링
            filtered = [
                r for r in shop_rows
                if start_code <= r["store_code"] <= end_code
            ]
            logging.info(f"[부분 실행] 범위: {start_code} ~ {end_code}. 총 {len(filtered)}개 매장")
            shop_rows = filtered
        except ValueError:
            messagebox.showerror("오류", "부분 실행 범위가 올바르지 않습니다. 예: STORE00001, STORE00005")
            return

        if not shop_rows:
            messagebox.showinfo("정보", "해당 범위 내 매장이 없습니다.")
            return
    else:
        logging.info("[전체 실행] 모든 매장 처리")

    logging.info(f"[총 매장 수] {len(shop_rows)}개")

    # 2) 하나씩 처리
    for row in shop_rows:
        try:
            driver = initialize_driver()
            if not driver:
                messagebox.showerror("오류", "브라우저 초기화 실패")
                return
                
            process_yogiyo_store(driver, row)
        except Exception as ex:
            logging.error(f"[전체처리오류] store_code={row.get('store_code','Unknown')}, {ex}")
            save_error_log_to_supabase(
                category="오류",
                store_code=row.get("store_code", "Unknown"),
                error_type="전체 처리 오류",
                error_message=str(ex),
                stack_trace=traceback.format_exc()
            )
        finally:
            if driver:
                driver.quit()

    messagebox.showinfo("완료", "요기요 리뷰 처리가 모두 완료되었습니다.")

###################################################################
# 15) GUI
###################################################################
root = tk.Tk()
root.title("요기요 리뷰 자동화 (Supabase)")

driver_label = tk.Label(root, text=f"크롬드라이버 경로: {driver_path or '미설정'}")
driver_label.pack(pady=10)

def set_driver_path():
    global driver_path
    path = filedialog.askopenfilename(
        title="크롬드라이버 경로", 
        filetypes=[("ChromeDriver","*.exe"),("All Files","*.*")]
    )
    if path:
        driver_label.config(text=f"크롬드라이버 경로: {path}")
        cfg = load_config()
        cfg["chromedriver_path"] = path
        save_config(cfg)
        driver_path = path

btn_set = tk.Button(root, text="크롬드라이버 경로 설정", command=set_driver_path)
btn_set.pack(pady=5)

# 실행 모드: 전체 / 부분
execution_mode = tk.StringVar(value='all')  # "all" 또는 "partial"

frame_mode = tk.LabelFrame(root, text="실행 범위 설정", padx=10, pady=10)
frame_mode.pack(pady=5)

rb_all = tk.Radiobutton(frame_mode, text="전체 실행", variable=execution_mode, value='all')
rb_all.grid(row=0, column=0, sticky="w")

rb_partial = tk.Radiobutton(frame_mode, text="부분 실행", variable=execution_mode, value='partial')
rb_partial.grid(row=0, column=1, sticky="w")

range_label = tk.Label(frame_mode, text="StoreCode 범위 (예: STORE00001, STORE00005)")
range_label.grid(row=1, column=0, columnspan=2, sticky="w")

range_entry = tk.Entry(frame_mode, width=30)
range_entry.grid(row=2, column=0, columnspan=2, pady=5, sticky="w")

btn_run = tk.Button(root, text="자동화 실행", command=run_automation)
btn_run.pack(pady=20)

if __name__=="__main__":
    root.mainloop()