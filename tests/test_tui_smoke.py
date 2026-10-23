"""Boot the real Textual app in --demo and walk every panel F1..F12 + h."""
import asyncio

import app
from mongodb_brabo.core.config import AppConfig
from mongodb_brabo.widgets.connection_pane import ConnectionPane, PANEL_KEYS

KEYS = ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12", "h"]


def test_demo_navigates_all_panels(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    async def scenario():
        application = app.MdbBraboApp([AppConfig(demo=True, refresh_interval=1)])
        async with application.run_test(size=(200, 60)) as pilot:
            await pilot.pause(1.0)
            pane = application.query_one(ConnectionPane)
            for key, panel in zip(KEYS, PANEL_KEYS):
                await pilot.press(key)
                await pilot.pause(0.7)
                assert pane.active_panel == panel, (key, pane.active_panel)
            # READ ONLY: kill on F2 must be refused without a modal.
            await pilot.press("f2")
            await pilot.pause(0.5)
            application.query_one(f"#panel-sessions-{pane.session.id}").action_kill()
            await pilot.pause(0.2)
            assert application.screen is application.screen_stack[0]
            await pilot.press("question_mark")
            await pilot.pause(0.3)
            assert application.screen.__class__.__name__ == "HelpScreen"
            await pilot.press("escape")
            sess = application.session_for_tests()
            assert sess.cache.get("demo") is True
            assert sess.cache.get("advisor.summary") is not None
        return True

    assert asyncio.run(scenario())
    # BasePanel.tick() swallows render errors (by design) and logs them —
    # so the smoke test must look at the log, not just for exceptions.
    errors = [r.getMessage() for r in caplog.records if r.name.startswith('mongodb_brabo')]
    assert errors == []
