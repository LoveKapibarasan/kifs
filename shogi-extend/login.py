import os
import pickle
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

COOKIE_FILE = "shogi_cookies.pkl"
LOGIN_URL = "https://www.shogi-extend.com/xusers/sign_in"
HOME_URL = "https://www.shogi-extend.com/"

# --- .env 読み込み ---
load_dotenv()
EMAIL = os.getenv("SHOGI_EMAIL")
PASSWORD = os.getenv("SHOGI_PASSWORD")

def save_cookies(driver, path):
    with open(path, "wb") as f:
        pickle.dump(driver.get_cookies(), f)

def load_cookies(driver, path):
    with open(path, "rb") as f:
        cookies = pickle.load(f)
        for cookie in cookies:
            cookie.pop("expiry", None)  # expiry が float の場合エラー回避
            driver.add_cookie(cookie)

def get_logged_in_driver():
    """ログイン済み Selenium driver を返す"""
    options = webdriver.ChromeOptions()
     # options.add_argument("--headless=new") 
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(service=Service(), options=options)

    if os.path.exists(COOKIE_FILE):
        # Cookie ロード
        driver.get(HOME_URL)
        load_cookies(driver, COOKIE_FILE)
        driver.refresh()
        print("セッションを復元しました ✅")

    else:
        # 初回ログイン
        driver.get(LOGIN_URL)

        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "xuser_email"))
        ).send_keys(EMAIL)

        driver.find_element(By.ID, "xuser_password").send_keys(PASSWORD)

        remember_me = driver.find_element(By.ID, "xuser_remember_me")
        if not remember_me.is_selected():
            remember_me.click()

        driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()

        WebDriverWait(driver, 10).until(
            EC.url_changes(LOGIN_URL)
        )

        save_cookies(driver, COOKIE_FILE)
        print("ログインして Cookie を保存しました ✅")

    return driver

