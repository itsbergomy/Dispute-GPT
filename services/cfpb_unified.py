"""
Unified CFPB complaint filing service.
Merges cfpb_automation.py and dispute_agent.py into a single, parameterized service.
Uses Playwright with SOP-driven validation from dispute_agent.py's pattern.
"""

import os
import json
import asyncio
import logging
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# Default SOP path
DEFAULT_SOP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cfpb_complaint_flow.json')
STORAGE_FILE = "cfpb-auth.json"
USER_DATA_DIR = "playwright_user_data"


class CFPBFiler:
    """
    Automated CFPB complaint filer using Playwright.

    Usage:
        filer = CFPBFiler()
        result = asyncio.run(filer.file_complaint({
            'narrative': '...',
            'resolution': '...',
            'company': '...',
            'account_number': '...',
            'address': {'line1': '...', 'city': '...', 'state': '...', 'zip': '...'},
        }))
    """

    def __init__(self, sop_path=None, headless=False):
        sop_file = sop_path or DEFAULT_SOP_PATH
        if os.path.exists(sop_file):
            with open(sop_file) as f:
                self.sop = json.load(f).get("steps", [])
        else:
            self.sop = []
        self.headless = headless

    async def file_complaint(self, complaint_data):
        """
        File a CFPB complaint with the given data.

        Args:
            complaint_data: Dict with keys:
                - narrative: str — "Tell us what happened" text
                - resolution: str — "What would be a fair resolution" text
                - company: str — Collection company name
                - account_number: str — Account number being disputed
                - address: dict with line1, line2 (optional), city, state, zip
                - name: str (optional) — Complainant name

        Returns:
            Dict with 'status': 'submitted'|'failed' and optional 'error'.
        """
        async with async_playwright() as p:
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=USER_DATA_DIR,
                    headless=self.headless,
                    slow_mo=500,
                )

                # Load saved auth state if available
                if os.path.exists(STORAGE_FILE):
                    try:
                        await context.add_cookies(
                            json.load(open(STORAGE_FILE)).get('cookies', [])
                        )
                    except Exception:
                        pass

                page = await context.new_page()

                # Execute complaint flow
                steps = [
                    ('navigate', self._navigate_and_login),
                    ('select_debt', self._select_debt_collection),
                    ('select_problem', self._select_problem_type),
                    ('fix_problem', self._answer_fix_problem_questions),
                    ('fill_narrative', self._fill_narrative_and_resolution),
                    ('fill_company', self._fill_company_and_account),
                    ('who_submitted', self._select_who_submitted),
                    ('fill_address', self._fill_personal_info),
                    ('submit', self._review_and_submit),
                ]

                for step_name, step_fn in steps:
                    success = await self._execute_with_retry(
                        page, step_name, step_fn, complaint_data
                    )
                    if not success:
                        # Save auth state even on failure
                        await context.storage_state(path=STORAGE_FILE)
                        return {'status': 'failed', 'error': f'Step "{step_name}" failed after retries'}

                # Save auth state on success
                await context.storage_state(path=STORAGE_FILE)
                await context.close()

                return {'status': 'submitted'}

            except Exception as e:
                logger.exception("CFPB filing failed")
                return {'status': 'failed', 'error': str(e)}

    async def _execute_with_retry(self, page, step_name, step_fn, data, retries=3):
        """Execute a step with retry logic and SOP validation."""
        for attempt in range(retries):
            try:
                await step_fn(page, data)

                # Validate against SOP if available
                sop_step = self._get_sop_step(step_name)
                if sop_step and not await self._validate_step(page, sop_step.get('validation', {})):
                    logger.warning(f"[{step_name}] validation failed (attempt {attempt + 1})")
                    continue

                logger.info(f"[{step_name}] completed (attempt {attempt + 1})")
                return True

            except PlaywrightTimeoutError:
                logger.warning(f"[{step_name}] timeout (attempt {attempt + 1})")
            except Exception as e:
                logger.warning(f"[{step_name}] error: {e} (attempt {attempt + 1})")

        return False

    def _get_sop_step(self, step_name):
        """Find a step in the SOP JSON by name."""
        for step in self.sop:
            if step.get("name") == step_name:
                return step
        return None

    async def _validate_step(self, page, validation_rules):
        """Validate a step against SOP-defined rules."""
        if not validation_rules:
            return True

        if "url_contains" in validation_rules:
            if validation_rules["url_contains"] not in page.url:
                return False

        if "element_presence" in validation_rules:
            for selector_text in validation_rules["element_presence"]:
                try:
                    await page.wait_for_selector(f"text={selector_text}", timeout=5000)
                except PlaywrightTimeoutError:
                    return False

        if validation_rules.get("textarea_nonempty"):
            try:
                textareas = await page.locator("textarea").all()
                for ta in textareas:
                    val = await ta.input_value()
                    if not val.strip():
                        return False
            except Exception:
                return False

        return True

    # ─── Step Implementations ───

    async def _navigate_and_login(self, page, data):
        """Navigate to CFPB and wait for the complaint form."""
        await page.goto("https://www.consumerfinance.gov/complaint/")
        await page.wait_for_selector(
            "text=What is this complaint about?",
            timeout=120_000  # 2 minutes for login
        )

    async def _select_debt_collection(self, page, data):
        """Select 'Debt collection' and 'I do not know'."""
        await page.click("label:has-text('Debt collection')")
        await page.wait_for_selector("text=What type of debt?", timeout=10_000)
        await page.click("label:has-text('I do not know')")
        await self._click_next(page)

    async def _select_problem_type(self, page, data):
        """Select problem type options."""
        await page.wait_for_selector("text=What type of problem", timeout=10_000)
        await page.click("label:has-text('Took or threatened to take negative or legal action')")
        await page.wait_for_selector("text=Which best describes", timeout=10_000)
        await page.click("label:has-text('Threatened or suggested your credit would be damaged')")
        await self._click_next(page)

    async def _answer_fix_problem_questions(self, page, data):
        """Answer Yes, Yes, No to the fix-problem questions."""
        # Q1: Have you tried to fix this?
        await page.wait_for_selector("text=Have you already tried to fix", timeout=10_000)
        await page.locator("label:has-text('Yes')").first.click()
        await self._click_next(page)

        # Q2: Did you request information?
        await page.wait_for_selector("text=Did you request information", timeout=10_000)
        await page.locator("label:has-text('Yes')").first.click()
        await self._click_next(page)

        # Q3: Did the company provide info?
        await page.wait_for_selector("text=Did the company provide", timeout=10_000)
        await page.locator("label:has-text('No')").first.click()
        await self._click_next(page)

    async def _fill_narrative_and_resolution(self, page, data):
        """Fill 'Tell us what happened' and 'fair resolution' textareas."""
        await page.wait_for_selector("text=Tell us what happened", timeout=30_000)
        textareas = page.locator("textarea")
        await textareas.nth(0).fill(data.get('narrative', ''))
        await textareas.nth(1).fill(data.get('resolution', ''))
        await self._click_next(page)

    async def _fill_company_and_account(self, page, data):
        """Fill company name and account number."""
        await page.wait_for_selector("text=Collection company", timeout=10_000)
        inputs = page.locator("input[type='text']")
        await inputs.nth(0).fill(data.get('company', ''))
        await inputs.nth(1).fill(data.get('account_number', ''))
        await page.click("label:has-text(\"No / I don't know\")")
        await self._click_next(page)

    async def _select_who_submitted(self, page, data):
        """Select 'Myself'."""
        await page.wait_for_selector("text=Who are you submitting", timeout=10_000)
        await page.click("label:has-text('Myself')")
        await self._click_next(page)

    async def _fill_personal_info(self, page, data):
        """Fill mailing address fields."""
        address = data.get('address', {})
        await page.fill("input[name='addressLine1']", address.get('line1', ''))
        if address.get('line2'):
            await page.fill("input[name='addressLine2']", address['line2'])
        await page.fill("input[name='city']", address.get('city', ''))
        await page.select_option("select[name='state']", label=address.get('state', ''))
        await page.fill("input[name='zip']", address.get('zip', ''))
        await self._click_next(page)

    async def _review_and_submit(self, page, data):
        """Submit on the final review page."""
        await page.wait_for_selector("text=Review your complaint", timeout=30_000)
        await page.click("button:has-text('Submit')")

    async def _click_next(self, page):
        """Click Next/Continue button."""
        for selector in ("button:has-text('Next')", "button:has-text('Continue')"):
            try:
                await page.wait_for_selector(selector, state="visible", timeout=5_000)
                await page.click(selector)
                return
            except PlaywrightTimeoutError:
                continue
        raise RuntimeError("Could not find Next/Continue button")


def file_cfpb_complaint_sync(complaint_data):
    """Synchronous wrapper for filing a CFPB complaint."""
    filer = CFPBFiler(headless=True)
    return asyncio.run(filer.file_complaint(complaint_data))
