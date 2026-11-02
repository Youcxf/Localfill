import os
import sqlite3

from app.config import Settings


# forgot the dashboard password? run: python -m app.reset_password
# it removes the password so the dashboard asks you to choose a new one
def main():
    settings = Settings()
    path = os.path.join(settings.data_dir, "localfill.db")
    if not os.path.exists(path):
        print(f"no database found at {path}")
        return
    db = sqlite3.connect(path)
    db.execute("delete from settings where key = 'admin_password'")
    db.commit()
    db.close()
    print("password removed. restart localfill, then open the dashboard to choose a new one.")


if __name__ == "__main__":
    main()
