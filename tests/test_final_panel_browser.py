"""Browser regression for the new draft workflow. Uses the isolated Django live server."""
import pytest
import asyncio
import sys
from django.test import Client
from django.conf import settings
from playwright.sync_api import sync_playwright, expect
from tests.test_final_panel_workflow import live

@pytest.mark.django_db(transaction=True)
def test_browser_drafts_refresh_and_chief_live_visibility(live, live_server, monkeypatch):
    if sys.platform == 'win32':
        monkeypatch.setattr(asyncio, 'new_event_loop', asyncio.ProactorEventLoop)
    _,_,_,panel,_,users,_,_=live
    judge_client=Client();judge_client.force_login(users['d1_judge'])
    chief_client=Client();chief_client.force_login(users['chief_judge'])
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        judge=browser.new_context(viewport={'width':1440,'height':1000})
        chief=browser.new_context(viewport={'width':1440,'height':1000})
        for context,client in [(judge,judge_client),(chief,chief_client)]:
            context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,
                'value':client.cookies[settings.SESSION_COOKIE_NAME].value,'url':live_server.url}])
        page=judge.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(live_server.url+f'/scoring/traditional-d/?panel_id={panel.pk}')
        page.locator('[name=posture_score]').fill('5.25')
        expect(page.locator('#save-status')).to_have_text('Draft saved in database.')
        page.locator('[name=stability_score]').fill('1.50')
        expect(page.locator('#save-status')).to_have_text('Draft saved in database.')
        page.locator('#next').click()
        expect(page.locator('#pose')).to_have_text('Asana 2')
        page.locator('[name=posture_score]').fill('4.25')
        expect(page.locator('#save-status')).to_have_text('Draft saved in database.')
        page.locator('#previous').click()
        expect(page.locator('[name=posture_score]')).to_have_value('5.25')
        page.locator('[name=posture_score]').fill('5.75')
        expect(page.locator('#save-status')).to_have_text('Draft saved in database.')
        page.reload();expect(page.locator('[name=posture_score]')).to_have_value('5.75')
        page.locator('#athlete').select_option('1')
        page.locator('[name=posture_score]').fill('3.00')
        expect(page.locator('#save-status')).to_have_text('Draft saved in database.')
        page.locator('#athlete').select_option('0')
        expect(page.locator('[name=posture_score]')).to_have_value('5.75')
        review=chief.new_page();review.on('pageerror',lambda e:errors.append(str(e)))
        review.goto(live_server.url+f'/scoring/chief-judge/?panel_id={panel.pk}')
        expect(review.locator('#live')).to_contain_text('Athlete0')
        expect(review.locator('#live')).not_to_contain_text('Athlete2')
        page.locator('[name=posture_score]').fill('5.50')
        expect(page.locator('#save-status')).to_have_text('Draft saved in database.')
        expect(review.locator('#live')).to_contain_text('posture score: 5.50',timeout=10000)
        for seq in range(2,8):
            page.locator('#next').click()
            page.locator('[name=posture_score]').fill('5.00')
            page.locator('[name=stability_score]').fill('1.00')
            expect(page.locator('#save-status')).to_have_text('Draft saved in database.')
        page.locator('#review').click();expect(page.locator('#review-area')).to_be_visible()
        page.locator('#submit').click();expect(page.locator('#sheet-status')).to_have_text('Submitted and locked')
        expect(page.locator('[name=posture_score]')).to_be_disabled()
        page.reload();expect(page.locator('#sheet-status')).to_have_text('Submitted and locked')
        assert errors==[]
        browser.close()
