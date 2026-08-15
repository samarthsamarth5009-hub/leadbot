import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = next(
    path for path in Path(__file__).parent.glob("*.py")
    if path.name not in {Path(__file__).name}
)


class FakeBot:
    def __init__(self, token, bot_id=None, username=None):
        self.token = token
        token_id = token.split(":", 1)[0]
        self.id = bot_id if bot_id is not None else (
            int(token_id) if token_id.isdigit() else abs(hash(token)) % 10_000_000
        )
        self.username = username or f"bot_{self.id}"


class FakeUpdater:
    def __init__(self, running=False):
        self.running = running


class FakeApplication:
    def __init__(self, token):
        self.bot = FakeBot(token)
        self.handlers = []
        self.running = False
        self.updater = FakeUpdater()

    def add_handler(self, handler):
        self.handlers.append(handler)


class FakeApplicationBuilder:
    def __init__(self):
        self._token = None

    def token(self, token):
        self._token = token
        return self

    def request(self, _request):
        return self

    def build(self):
        return FakeApplication(self._token)


class FakeApplicationFactory:
    @staticmethod
    def builder():
        return FakeApplicationBuilder()


class FakeCommandHandler:
    def __init__(self, command, callback):
        commands = [command] if isinstance(command, str) else command
        self.commands = frozenset(commands)
        self.callback = callback


class FakeMessageHandler:
    def __init__(self, message_filter, callback):
        self.message_filter = message_filter
        self.callback = callback


class FakeInlineKeyboardButton:
    def __init__(self, text, url=None):
        self.text = text
        self.url = url


class FakeInlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeUpdate:
    def __init__(self, user_id):
        self.effective_user = types.SimpleNamespace(id=user_id)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, token, bot_id=1, args=None):
        self.bot = FakeBot(token, bot_id=bot_id)
        self.args = [] if args is None else args


class FakeTelegramError(Exception):
    pass


class FakeRetryAfter(FakeTelegramError):
    def __init__(self, retry_after=0):
        self.retry_after = retry_after


def dependency_stubs():
    requests = types.ModuleType("requests")
    telegram = types.ModuleType("telegram")
    constants = types.ModuleType("telegram.constants")
    errors = types.ModuleType("telegram.error")
    ext = types.ModuleType("telegram.ext")
    request = types.ModuleType("telegram.request")

    telegram.Update = object
    telegram.InlineKeyboardMarkup = FakeInlineKeyboardMarkup
    telegram.InlineKeyboardButton = FakeInlineKeyboardButton
    telegram.ChatPermissions = lambda **kwargs: kwargs
    telegram.ReactionTypeEmoji = lambda **kwargs: kwargs

    constants.ChatType = types.SimpleNamespace(
        PRIVATE="private", GROUP="group", SUPERGROUP="supergroup"
    )
    errors.TelegramError = FakeTelegramError
    errors.RetryAfter = FakeRetryAfter
    errors.TimedOut = FakeTelegramError
    errors.NetworkError = FakeTelegramError

    ext.Application = FakeApplicationFactory
    ext.CommandHandler = FakeCommandHandler
    ext.MessageHandler = FakeMessageHandler
    ext.CallbackQueryHandler = object
    ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    ext.filters = types.SimpleNamespace(ALL=object())
    request.HTTPXRequest = lambda **kwargs: kwargs

    telegram.constants = constants
    telegram.error = errors
    telegram.ext = ext
    telegram.request = request
    return {
        "requests": requests,
        "telegram": telegram,
        "telegram.constants": constants,
        "telegram.error": errors,
        "telegram.ext": ext,
        "telegram.request": request,
    }


def load_leadbot_module():
    spec = importlib.util.spec_from_file_location("leadbot_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependency_stubs()):
        spec.loader.exec_module(module)
    return module


class LeadVsCloneRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_leadbot_module()

    @staticmethod
    def commands_for(app):
        commands = set()
        for handler in app.handlers:
            commands.update(getattr(handler, "commands", ()))
        return commands

    def test_control_plane_commands_registered_only_on_lead_bot(self):
        lead_app = self.module.build_app(self.module.LEAD_BOT_TOKEN)
        clone_app = self.module.build_app("123456789:clone_token_value")
        lead_commands = self.commands_for(lead_app)
        clone_commands = self.commands_for(clone_app)
        lead_only = {"clone", "mirror", "addallbots", "allbotadd"}

        self.assertTrue(lead_only.issubset(lead_commands))
        self.assertTrue(lead_only.isdisjoint(clone_commands))

    def test_runtime_lead_guard_blocks_clone_execution(self):
        update = FakeUpdate(self.module.OWNER_ID)
        clone_context = FakeContext("123456789:clone_token_value")

        asyncio.run(self.module.clone_bot(update, clone_context))

        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("sirf lead bot", update.message.replies[0][0])

    def test_addallbots_buttons_are_admin_links_and_batched(self):
        running_apps = []
        for index in range(self.module.ADDALLBOTS_BATCH_SIZE + 1):
            app = FakeApplication(f"{1000 + index}:token")
            app.bot = FakeBot(
                app.bot.token, bot_id=1000 + index, username=f"running_bot_{index}"
            )
            app.running = True
            app.updater.running = True
            running_apps.append(app)

        update = FakeUpdate(self.module.OWNER_ID)
        lead_context = FakeContext(self.module.LEAD_BOT_TOKEN)
        original_apps = self.module.apps
        self.module.apps = running_apps
        try:
            asyncio.run(self.module.addallbots(update, lead_context))
        finally:
            self.module.apps = original_apps

        self.assertEqual(len(update.message.replies), 2)
        first_markup = update.message.replies[0][1]["reply_markup"]
        second_markup = update.message.replies[1][1]["reply_markup"]
        self.assertEqual(len(first_markup.inline_keyboard), self.module.ADDALLBOTS_BATCH_SIZE)
        self.assertEqual(len(second_markup.inline_keyboard), 1)
        for text, kwargs in update.message.replies:
            self.assertIn("Batch", text)
            for row in kwargs["reply_markup"].inline_keyboard:
                url = row[0].url
                self.assertIn("?startgroup&admin=", url)
                self.assertIn("promote_members", url)
                self.assertIn("manage_chat", url)

    def test_help_hides_lead_commands_on_clone(self):
        lead_update = FakeUpdate(self.module.OWNER_ID)
        clone_update = FakeUpdate(self.module.OWNER_ID)

        asyncio.run(self.module.help_cmd(
            lead_update, FakeContext(self.module.LEAD_BOT_TOKEN)
        ))
        asyncio.run(self.module.help_cmd(
            clone_update, FakeContext("123456789:clone_token_value")
        ))

        lead_help = lead_update.message.replies[-1][0]
        clone_help = clone_update.message.replies[-1][0]
        for command in ("/clone", "/mirror", "/addallbots"):
            self.assertIn(command, lead_help)
            self.assertNotIn(command, clone_help)

    def test_mync_loop_is_registered_after_definition(self):
        self.assertIs(
            self.module.NC_LOOP_REGISTRY["mync_loop"], self.module.mync_loop
        )


if __name__ == "__main__":
    unittest.main()
