import os
import sys
import time
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = BASE_DIR / "docs" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# 40 Screen Route Mapping
SCREENS = [
    ("01_Login", "/accounts/login/", 1280, 800, False),
    ("02_Admin_Dashboard", "/dashboard/", 1440, 900, True),
    ("03_Championship_Setup", "/championships/1/setup/", 1440, 900, True),
    ("04_Users_and_Permissions", "/accounts/users/", 1440, 900, True),
    ("05_Athlete_Personal_Details", "/register/athlete/step-1/", 1280, 850, False),
    ("06_Event_and_Pair_Selection", "/register/athlete/step-2/", 1280, 850, False),
    ("07_Unrestricted_Asana_Selection", "/register/athlete/step-3/", 1440, 900, False),
    ("08_Athlete_Status_and_ID_Card", "/athletes/id-card/TYSA-ATH-00101/", 1280, 850, False),
    ("09_Universal_Judge_Registration", "/judges/apply/", 1280, 850, False),
    ("10_Judge_Profile_and_Assigned_Panels", "/judges/1/", 1440, 900, True),
    ("11_Admin_Volunteer_Guest_COACH_Registers", "/registers/", 1440, 900, True),
    ("12_District_Athlete_Approval", "/district/approvals/", 1440, 900, True),
    ("13_Venue_Verification", "/venue/verification/", 1440, 900, True),
    ("14_District_Draw_and_Master_Roll", "/competition/draw/", 1440, 900, True),
    ("15_CD_Judge_Panel_Assignment", "/competition/panels/", 1440, 900, True),
    ("16_Stage_Manager", "/competition/stage/", 1440, 900, True),
    ("16.1_Second_Screen", "/display/second-screen/", 1920, 1080, False),
    ("17_Traditional_D_Judge", "/scoring/traditional-d/", 1440, 900, True),
    ("18_Artistic_Single_D_Judge", "/scoring/artistic-single-d/", 1440, 900, True),
    ("19_Artistic_Pair_D_Judge", "/scoring/artistic-pair-d/", 1440, 900, True),
    ("20_Rhythmic_Pair_D_Judge", "/scoring/rhythmic-pair-d/", 1440, 900, True),
    ("21_Music_Event_Time_Judge", "/scoring/music-time/", 1440, 900, True),
    ("22_Artistry_Judge", "/scoring/artistry/", 1440, 900, True),
    ("23_Evaluator_Valid_Invalid", "/scoring/evaluator/", 1440, 900, True),
    ("24_Chief_Judge_Review", "/scoring/chief-judge/", 1440, 900, True),
    ("25_Jury", "/jury/", 1440, 900, True),
    ("26_CD_Correction_and_Finalisation", "/cd/corrections/", 1440, 900, True),
    ("27_Public_Live_Score_Display", "/display/live/", 1920, 1080, False),
    ("28_Certificates_and_Printing", "/results/scoreboard/", 1440, 900, True),
    ("29_Statistics_and_Headcounts", "/reports/statistics/", 1440, 900, True),
    ("30_Overall_Championship_Trophies", "/reports/trophies/", 1440, 900, True),
    ("31_Ceremonies_Photos_and_Press", "/reports/ceremonies/", 1440, 900, True),
    ("32_Complete_Championship_Report", "/reports/championship/", 1440, 900, True),
    ("33_Traditional_Time_Judge", "/scoring/traditional-time/", 1440, 900, True),
    ("34_Championship_Basic_Details", "/championships/create/", 1440, 900, True),
    ("35_Traditional_Final_Multiple_Athletes", "/scoring/traditional-d-final/", 1440, 900, True),
    ("ASANA", "/catalogue/", 1440, 900, True),
    ("Certificate_Template_Library", "/certificates/templates/", 1440, 900, True),
    ("Certificate_Canvas_Editor", "/certificates/templates/1/edit/", 1440, 900, True),
    ("Certificate_Generate_Export", "/certificates/generate/", 1440, 900, True),
]

def run():
    print("Launching Django dev server...")
    server_process = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", "127.0.0.1:8000", "--noreload"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(3)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()

            # Sign in as Admin to establish authenticated session
            page = context.new_page()
            page.goto("http://127.0.0.1:8000/accounts/login/")
            page.fill("input[name='email']", "admin@tysa.org")
            page.fill("input[name='password']", "admin123")
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")

            print(f"Capturing all {len(SCREENS)} screens...")
            for name, path, width, height, auth in SCREENS:
                url = f"http://127.0.0.1:8000{path}"
                target_page = page if auth else context.new_page()
                target_page.set_viewport_size({"width": width, "height": height})
                try:
                    target_page.goto(url, wait_until="networkidle", timeout=15000)
                    time.sleep(0.5)
                    output_file = SCREENSHOT_DIR / f"{name}.png"
                    target_page.screenshot(path=str(output_file), full_page=True)
                    print(f"Captured: {name} -> {output_file.name}")
                except Exception as e:
                    print(f"Error on {name}: {e}")
                if not auth:
                    target_page.close()

            browser.close()
    finally:
        server_process.terminate()
        server_process.wait()
    print("All screenshots successfully captured!")

if __name__ == "__main__":
    run()
