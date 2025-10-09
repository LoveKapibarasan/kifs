import time
import os
from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv()
SWARS_ID = os.getenv("SWARS_ID")


def set_initial_value(driver):
    try:
        driver.get("https://www.shogi-extend.com/lab/swars/search-default")     
        wait = WebDriverWait(driver, 30)
    
        # --- 将棋ウォーズIDを入力 ---
        swars_id_input = wait.until(
            EC.presence_of_element_located((By.ID, "form_part-swars_search_default_key"))
        )
        swars_id_input.clear()
        driver.execute_script("arguments[0].value = '';", swars_id_input)
        swars_id_input.send_keys(SWARS_ID)
        reserve_button=  wait.until(
          EC.element_to_be_clickable((By.CSS_SELECTOR, "button.post_handle.is-primary"))
          )
        reserve_button.click()
        print("Updated query initial value.")
        driver.get("https://www.shogi-extend.com/swars/search")
        time.sleep(10)
    finally:
        print("set_initial_value done")

