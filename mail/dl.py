import os
import imaplib
import email
import zipfile
import shutil
from dotenv import load_dotenv

# --- .env から認証情報読み込み ---
load_dotenv()
EMAIL = os.getenv("MAIL_USER")
PASSWORD = os.getenv("MAIL_PASS")
IMAP_SERVER = os.getenv("IMAP_SERVER")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))

SAVE_DIR = os.getenv("SAVE_DIR", "kif_files")
os.makedirs(SAVE_DIR, exist_ok=True)

def fetch_and_extract_kif():
    print("MAIL_USER =", EMAIL)
    print("MAIL_PASS =", "*" * len(PASSWORD) if PASSWORD else None)

    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL, PASSWORD)

    mail.select("INBOX")

    typ, data = mail.search(None, "ALL")
    for num in data[0].split():
        typ, msg_data = mail.fetch(num, "(RFC822)")
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                filename = part.get_filename()
                if filename and filename.endswith(".zip"):
                    zip_path = os.path.join(SAVE_DIR, filename)

                    # ZIP保存
                    with open(zip_path, "wb") as f:
                        f.write(part.get_payload(decode=True))
                    print(f"ZIP保存: {zip_path}")

                    # ZIPを展開
                    tmp_extract = os.path.join(SAVE_DIR, "_tmp_extract")
                    os.makedirs(tmp_extract, exist_ok=True)
                    with zipfile.ZipFile(zip_path, "r") as z:
                        z.extractall(tmp_extract)

                    # .kif を SAVE_DIR に移動
                    for root, _, files in os.walk(tmp_extract):
                        for name in files:
                            if name.endswith(".kif"):
                                src = os.path.join(root, name)
                                dst = os.path.join(SAVE_DIR, name)
                                shutil.move(src, dst)
                                print(f"KIF抽出: {dst}")

                    # 後始末：zip とサブフォルダ削除
                    os.remove(zip_path)
                    shutil.rmtree(tmp_extract, ignore_errors=True)

        # 処理済みメールを削除
        mail.store(num, "+FLAGS", "\\Deleted")

    mail.expunge()
    mail.logout()

if __name__ == "__main__":
    fetch_and_extract_kif()
