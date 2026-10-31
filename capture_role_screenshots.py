import os
import time
from playwright.sync_api import sync_playwright

SCREENSHOTS_DIR = os.path.abspath(r"C:\Users\My Files\Desktop\tysa-score\screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

BASE_URL = "http://127.0.0.1:8027"

ROLES_CONFIG = [
    {
        'id': '01_system_admin',
        'name': 'System Admin',
        'email': 'admin@tysa.org',
        'password': 'admin123',
        'target_url': f"{BASE_URL}/dashboard/",
        'filename': '01_system_admin_dashboard.png'
    },
    {
        'id': '02_competition_director',
        'name': 'Competition Director (CD)',
        'email': 'cd@tysa.org',
        'password': 'cd123',
        'target_url': f"{BASE_URL}/cd/dashboard/",
        'filename': '02_cd_dashboard.png'
    },
    {
        'id': '03_district_user',
        'name': 'District User (Hyderabad)',
        'email': 'district@tysa.org',
        'password': 'district123',
        'target_url': f"{BASE_URL}/district/approvals/",
        'filename': '03_district_approvals.png'
    },
    {
        'id': '04_venue_desk',
        'name': 'Venue Clearance Desk',
        'email': 'venue@tysa.org',
        'password': 'venue123',
        'target_url': f"{BASE_URL}/venue/verification/",
        'filename': '04_venue_verification.png'
    },
    {
        'id': '05_stage_manager',
        'name': 'Stage Manager / Announcer',
        'email': 'stage@tysa.org',
        'password': 'stage123',
        'target_url': f"{BASE_URL}/competition/stage/",
        'filename': '05_stage_manager.png'
    },
    {
        'id': '06_chief_judge',
        'name': 'Chief Judge',
        'email': 'chief@tysa.org',
        'password': 'judge123',
        'target_url': f"{BASE_URL}/judges/portal/",
        'filename': '06_chief_judge_portal.png'
    },
    {
        'id': '07_d_judge',
        'name': 'Difficulty Judge (D1)',
        'email': 'd1@tysa.org',
        'password': 'judge123',
        'target_url': f"{BASE_URL}/judges/portal/",
        'filename': '07_d_judge_portal.png'
    },
    {
        'id': '08_d_judge_desk',
        'name': 'Traditional D Scoring Desk',
        'email': 'd1@tysa.org',
        'password': 'judge123',
        'target_url': f"{BASE_URL}/scoring/traditional-d/",
        'filename': '08_scoring_traditional_d_desk.png'
    },
    {
        'id': '09_chief_judge_review',
        'name': 'Chief Judge Review Desk',
        'email': 'chief@tysa.org',
        'password': 'judge123',
        'target_url': f"{BASE_URL}/scoring/chief-judge/",
        'filename': '09_chief_judge_review_desk.png'
    },
    {
        'id': '10_jury',
        'name': 'Jury Official',
        'email': 'jury@tysa.org',
        'password': 'jury123',
        'target_url': f"{BASE_URL}/jury/",
        'filename': '10_jury_review.png'
    },
    {
        'id': '11_multi_role',
        'name': 'Multi-Role User (Workspace Switcher)',
        'email': 'multirole@tysa.org',
        'password': 'multi123',
        'target_url': f"{BASE_URL}/accounts/role-select/",
        'filename': '11_multi_role_switcher.png'
    },
    {
        'id': '12_unassigned_official',
        'name': 'Unassigned Official / Pending Role',
        'email': 'arun.kumar@gmail.com',
        'password': 'password123',
        'target_url': f"{BASE_URL}/accounts/no-role/",
        'filename': '12_unassigned_no_role.png'
    },
]

def main():
    print("Launching Chromium for visual audit...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        for role in ROLES_CONFIG:
            print(f"Capturing: {role['name']} ({role['email']})...")
            context = browser.new_context(viewport={'width': 1440, 'height': 900})
            page = context.new_page()
            
            # Go to login
            page.goto(f"{BASE_URL}/accounts/login/")
            page.fill('input[name="email"]', role['email'])
            page.fill('input[name="password"]', role['password'])
            page.click('button[type="submit"]')
            page.wait_for_load_state('networkidle')
            
            # Navigate to target url if not already there
            if page.url != role['target_url']:
                page.goto(role['target_url'])
                page.wait_for_load_state('networkidle')
            
            time.sleep(1) # Allow Lucide icons and Tailwind styles to settle
            
            filepath = os.path.join(SCREENSHOTS_DIR, role['filename'])
            page.screenshot(path=filepath, full_page=False)
            print(f" -> Saved: {filepath}")
            context.close()

        # Capture Athlete Key-Access Workspace
        print("Capturing: Athlete Key-Access Workspace...")
        ath_context = browser.new_context(viewport={'width': 1440, 'height': 900})
        ath_page = ath_context.new_page()
        # Direct key access or session
        ath_page.goto(f"{BASE_URL}/athletes/id-card/TYSA-ATH-00101/")
        ath_page.wait_for_load_state('networkidle')
        time.sleep(1)
        ath_filepath = os.path.join(SCREENSHOTS_DIR, "13_athlete_key_workspace.png")
        ath_page.screenshot(path=ath_filepath, full_page=False)
        print(f" -> Saved: {ath_filepath}")
        ath_context.close()

        browser.close()
        print("\nAll role screenshots captured successfully in:", SCREENSHOTS_DIR)

if __name__ == '__main__':
    main()
