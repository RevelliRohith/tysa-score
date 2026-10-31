import os
import sys
import time
from playwright.sync_api import sync_playwright

ARTIFACT_DIR = r"C:\Users\My Files\.gemini\antigravity\brain\a897e001-e2c6-486a-bb23-a541ba8c66e1"
BASE_URL = "http://127.0.0.1:8027"

def run():
    print("Launching Playwright to capture verification screenshots on port 8027...")
    cert_uuid = sys.argv[1] if len(sys.argv) > 1 else "379cef3e-0dfc-434e-994f-aae535153f8b"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 900})
        page = context.new_page()

        def login(email, password):
            context.clear_cookies()
            page.goto(f"{BASE_URL}/accounts/login/")
            page.fill("input[name='email']", email)
            page.fill("input[name='password']", password)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")

        # 1. Admin / CD Dashboard
        print("1. Capturing Championship Dashboard...")
        login("admin@tysa.org", "AdminPass2026!")
        page.goto(f"{BASE_URL}/championships/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "01_championship_dashboard.png"), full_page=True)

        # 2. District Approvals & Quota Tracker
        print("2. Capturing District Approvals & Quotas...")
        login("district.hyderabad@tysa.org", "DistrictPass2026!")
        page.goto(f"{BASE_URL}/registrations/district-approvals/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "02_district_approvals_quota.png"), full_page=True)

        # 3. Venue Clearance Desk
        print("3. Capturing Venue Clearance Desk...")
        login("venue@tysa.org", "VenueDesk2026!")
        page.goto(f"{BASE_URL}/registrations/venue-verification/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "03_venue_clearance_exceptions.png"), full_page=True)

        # 4. District Lottery Draw
        print("4. Capturing District Lottery Draw...")
        login("cd@tysa.org", "CDDirector2026!")
        page.goto(f"{BASE_URL}/competition/draw/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "04_district_lottery_draw.png"), full_page=True)

        # 5. Master Roll
        print("5. Capturing Master Roll...")
        page.goto(f"{BASE_URL}/competition/master-roll/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "05_master_roll_chest_numbers.png"), full_page=True)

        # 6. Judge Panels Governance
        print("6. Capturing Judge Panels Governance...")
        page.goto(f"{BASE_URL}/competition/panels/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "06_judge_panels_governance.png"), full_page=True)

        # 7. Stage Manager Marshalling
        print("7. Capturing Stage Manager Marshalling...")
        login("stage@tysa.org", "StageManager2026!")
        page.goto(f"{BASE_URL}/competition/stage-manager/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "07_stage_manager_marshalling.png"), full_page=True)

        # 8. Digital Scoring Desk
        print("8. Capturing Digital Scoring Desk...")
        login("d1@tysa.org", "JudgePass2026!")
        page.goto(f"{BASE_URL}/scoring/traditional-d/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "08_digital_scoring_desk.png"), full_page=True)

        # 9. Chief Judge Desk
        print("9. Capturing Chief Judge Desk...")
        login("chief@tysa.org", "JudgePass2026!")
        page.goto(f"{BASE_URL}/scoring/chief-judge/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "09_chief_judge_review.png"), full_page=True)

        # 10. Live Arena Audience Display
        print("10. Capturing Live Arena Display...")
        page.goto(f"{BASE_URL}/results/live/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "10_live_arena_display.png"), full_page=True)

        # 11. Official Results & Medals Board
        print("11. Capturing Official Results & Medals...")
        page.goto(f"{BASE_URL}/results/")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(ARTIFACT_DIR, "11_official_results_medals.png"), full_page=True)

        # 12. Verifiable Issued Certificate Scan
        if cert_uuid:
            print("12. Capturing Verifiable Issued Certificate...")
            page.goto(f"{BASE_URL}/certificates/verify/{cert_uuid}/")
            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(ARTIFACT_DIR, "12_verified_issued_certificate.png"), full_page=True)

        browser.close()
        print("[OK] All 12 screenshots captured successfully!")

if __name__ == '__main__':
    run()
