import os
from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from login import get_logged_in_driver  
from load_kif import set_initial_value
load_dotenv()
SWARS_ID = os.getenv("SWARS_ID")

driver = get_logged_in_driver()

try:
    set_initial_value(driver)     
    driver.get("https://www.shogi-extend.com/lab/swars/crawler-batch")
    wait = WebDriverWait(driver, 30)
    # --- 将棋ウォーズIDを入力 ---
    swars_id_input = wait.until(
        EC.presence_of_element_located((By.ID, "form_part-swars_user_key"))
    )
    swars_id_input.clear()
    driver.execute_script("arguments[0].value = '';", swars_id_input)
    swars_id_input.send_keys(SWARS_ID)

    # --- ZIP添付を選択 ---
    zip_radio = driver.find_element(By.CSS_SELECTOR, "input[value='with_zip']")
    driver.execute_script("arguments[0].click();", zip_radio)

    # --- ローディングオーバーレイが消えるのを待機 ---
    wait.until(
        EC.invisibility_of_element_located((By.CSS_SELECTOR, ".loading-background"))
    )

    # --- 棋譜取得の予約ボタンをクリック ---
    reserve_button = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button.post_handle.is-primary"))
    )
    reserve_button.click()

    print("棋譜取得の予約を送信しました")

finally:
    driver.quit()  # 自動で閉じたいときだけ有効化
    pass

