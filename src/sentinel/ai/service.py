"""AIService: the single object the UI and controller use for everything AI.

* All network work runs on a dedicated one-worker JobRunner (bounded queue), never on the UI thread.
* ``send`` refuses unless AI is enabled AND the caller states that the user confirmed this exact request.
* Nothing is sent automatically, on a timer, per event, or from the wallpaper.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ..storage.database import Database, DatabaseError
from ..ui.jobs import JobRunner
from .config import AI_SETTING_KEYS, AIConfig, load_config
from .context_builder import ContextBuilder
from .conversation_store import ConversationStore
from .errors import AIError, ErrorKind
from .models import AIRequest, ChatMessage, ConnectionResult, Health, RouteResult
from .privacy import Redactor
from .providers import PROVIDER_IDS, make_provider
from .proxy import ProxyConfigError, ProxySettings, validate_host, validate_port
from .router import AIRouter
from .secrets import SecretStore
from .transport import CancelHandle, Transport
from .usage import UsageTracker

log = logging.getLogger("sentinel.ai")

MAX_LIVE_MESSAGES = 60
OFF_MESSAGE = ("AI is switched off, so Sentinel makes no network connection. Turn on 'Enable AI Assistant' first "
               "(this does not send anything).")
BUDGET_MARGIN_S = 10.0     # added to the sum of the provider timeouts to give the whole request a hard deadline


@dataclass
class ChatEntry:
    role: str
    content: str                 # exactly what was sent (user) / sanitised answer (assistant)
    display: str
    provider: str = ""
    model: str = ""
    meta: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class Conversation:
    id: Optional[int] = None
    title: str = ""
    entries: list[ChatEntry] = field(default_factory=list)
    approved: set = field(default_factory=set)       # context item ids already reviewed in this conversation


class AIService(QObject):
    changed = Signal()                 # config, key, health or usage changed
    activity = Signal(str)             # "idle" | "running" | "cancelling"
    context_requested = Signal(str, object, object)   # task, item id, event ids: another page asks to open the AI page

    def __init__(self, settings: Any, db: Database, parent: Optional[QObject] = None, *,
                 transport: Optional[Transport] = None, secrets: Optional[SecretStore] = None,
                 provider_factory: Callable[[str], Any] = make_provider) -> None:
        super().__init__(parent)
        self.settings = settings
        self.secrets = secrets or SecretStore()
        self.usage = UsageTracker()
        self.router = AIRouter(lambda: load_config(self.settings), self.secrets, self.usage,
                               transport=transport if transport is not None else Transport(self.proxy_settings),
                               provider_factory=provider_factory)
        self.store = ConversationStore(db)
        self.jobs = JobRunner(self, max_threads=1, max_pending=2)
        self.conversation = Conversation()
        self._handle: Optional[CancelHandle] = None
        self._active = False
        self._cancelling = False
        self._token = 0
        self._closed = False
        self.last_error: Optional[AIError] = None
        self.last_result: Optional[RouteResult] = None
        settings.subscribe(self._on_setting)

    def proxy_settings(self) -> Optional[ProxySettings]:
        """Current proxy for a request, or None for a direct connection. If a proxy is enabled but unusable this
        raises, so the request fails instead of silently bypassing the proxy."""
        g = self.settings.get
        if not g("ai_proxy_enabled"):
            return None
        try:
            host, port = validate_host(str(g("ai_proxy_host"))), validate_port(g("ai_proxy_port"))
        except ProxyConfigError as exc:
            raise AIError(ErrorKind.PROXY, str(exc)) from None
        if not g("ai_proxy_auth"):
            return ProxySettings(host, port)
        creds = self.secrets.proxy_credentials()
        if creds is None:
            raise AIError(ErrorKind.PROXY_AUTH, "no proxy user name and password are stored")
        return ProxySettings(host, port, creds[0], creds[1])

    def request_context(self, task: str, ident: Any = None, event_ids: Optional[list] = None) -> None:
        """"Ask AI about this" buttons call this. It only opens the AI page with the choice pre-selected; nothing is sent."""
        self.context_requested.emit(task, ident, event_ids or [])

    # ------------------------------------------------------------------ configuration
    def config(self) -> AIConfig:
        return load_config(self.settings)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("ai_enabled"))

    def builder(self) -> ContextBuilder:
        cfg = self.config()
        return ContextBuilder(Redactor(cfg.redaction), cfg.context_max_events)

    def _on_setting(self, key: str, value: Any) -> None:
        if not key.startswith("ai_"):
            return
        if key == "ai_enabled" and not value:
            self.cancel()
        if key.endswith(("_enabled", "_model", "_timeout")) or key in ("ai_primary", "ai_fallback_order") \
                or key.startswith("ai_proxy_"):
            self.router.provider_changed()
        self.changed.emit()

    # ------------------------------------------------------------------ keys and health
    def health(self, provider_id: str) -> Health:
        return self.router.health(provider_id)

    def set_key(self, provider_id: str, key: str) -> None:
        self.secrets.set_key(provider_id, key)
        self.router.provider_changed(provider_id)
        self.changed.emit()

    def delete_key(self, provider_id: str) -> bool:
        done = self.secrets.delete_key(provider_id)
        self.router.provider_changed(provider_id)
        self.changed.emit()
        return done

    def reset_configuration(self, delete_keys: bool = False) -> None:
        """Back to the defaults (AI off). Stored keys are removed only when *delete_keys* is True."""
        self.cancel()
        for key in AI_SETTING_KEYS:
            self.settings.reset(key)
        if delete_keys:
            for pid in PROVIDER_IDS:
                self.secrets.delete_key(pid)
            self.secrets.delete_proxy_credentials()
        self.router.provider_changed()
        self.usage.reset()
        self.changed.emit()

    # ------------------------------------------------------------------ conversation
    def new_conversation(self) -> None:
        self.conversation = Conversation()
        self.changed.emit()

    def history_for_request(self) -> list[ChatMessage]:
        n = self.config().history_messages
        entries = self.conversation.entries[-n:] if n else []
        while entries and entries[0].role != "user":
            entries = entries[1:]
        return [ChatMessage(e.role, e.content) for e in entries]

    def review_required(self, selected_ids: set[str]) -> bool:
        """Per-request review is the default. 'new_context_only' skips the dialog only for a follow-up in the same
        conversation whose context items were all reviewed before; any new item always requires a review."""
        cfg = self.config()
        if cfg.consent_mode != "new_context_only" or not self.conversation.entries:
            return True
        return not selected_ids <= self.conversation.approved

    def record_exchange(self, question_display: str, sent_text: str, result: RouteResult,
                        selected_ids: set[str]) -> None:
        resp = result.response
        meta = {"fallback": result.fallback_note(), "truncated": resp.truncated}
        conv = self.conversation
        conv.entries.append(ChatEntry("user", sent_text, question_display))
        conv.entries.append(ChatEntry("assistant", resp.text, resp.text, resp.provider_id, resp.model, meta))
        del conv.entries[:-MAX_LIVE_MESSAGES]
        conv.approved |= set(selected_ids)
        if self.config().history_enabled:
            try:
                if conv.id is not None and not self.store.exists(conv.id):
                    conv.id = None                       # it was deleted while open: start a stored copy afresh
                if conv.id is None:
                    conv.id = self.store.create(question_display[:60])
                self.store.add_message(conv.id, "user", sent_text, question_display)
                self.store.add_message(conv.id, "assistant", resp.text, resp.text, provider=resp.provider_id,
                                       model=resp.model, meta=meta)
            except DatabaseError:
                log.exception("conversation history was not saved")
        self.changed.emit()

    def load_conversation(self, conversation_id: int) -> bool:
        msgs = self.store.messages(conversation_id)
        if not msgs:
            return False
        title = next((c.title for c in self.store.list() if c.id == conversation_id), "")
        self.conversation = Conversation(conversation_id, title, [
            ChatEntry(m.role, m.content, m.display, m.provider, m.model, m.meta, m.ts) for m in msgs[-MAX_LIVE_MESSAGES:]])
        self.changed.emit()
        return True

    def delete_conversation(self, conversation_id: int) -> bool:
        done = self.store.delete(conversation_id)
        if self.conversation.id == conversation_id:
            self.conversation = Conversation()
        self.changed.emit()
        return done

    def clear_history(self) -> int:
        n = self.store.clear_all()
        self.conversation = Conversation()
        self.changed.emit()
        return n

    # ------------------------------------------------------------------ requests (background worker)
    @property
    def busy(self) -> bool:
        return self._active

    def send(self, request: AIRequest, on_done: Callable[[RouteResult], None],
             on_fail: Callable[[AIError], None], *, confirmed: bool, override: Optional[str] = None) -> None:
        """Start a request. Raises AIError immediately (nothing is sent) if disabled, unconfirmed or busy."""
        if self._closed or not self.enabled:
            raise AIError(ErrorKind.DISABLED, "AI is switched off")
        if not confirmed:
            raise AIError(ErrorKind.CONSENT)
        if self._active:
            raise AIError(ErrorKind.BUSY)
        cfg = self.config()
        chain, skipped = self.router.plan(cfg, override)
        if not chain:
            raise AIError(ErrorKind.NOT_CONFIGURED, "; ".join(f"{p}: {r}" for p, r in skipped) or "no provider selected")
        budget = sum(cfg.provider(p).timeout_s for p in chain) + BUDGET_MARGIN_S
        handle = CancelHandle()
        self._handle, self._active, self._cancelling = handle, True, False
        self._token += 1
        token = self._token
        self.usage.active, self.usage.queue_depth = 1, self.jobs.pending_count
        self.activity.emit("running")
        self.changed.emit()

        def work(_cancel, _progress):
            try:
                return self.router.route(request, handle, override)
            except AIError as exc:                      # AIError objects cross the thread boundary as values
                return exc
            except Exception as exc:                    # never let an unexpected error escape silently
                log.exception("AI request failed unexpectedly")
                return AIError(ErrorKind.UNKNOWN, type(exc).__name__)

        def finish(outcome: Any) -> None:
            if token != self._token:
                return
            self._active, self._handle = False, None
            was_cancel = self._cancelling
            self._cancelling = False
            self.usage.active = 0
            self.activity.emit("idle")
            if isinstance(outcome, RouteResult):
                if was_cancel:
                    outcome = AIError(ErrorKind.CANCELLED)       # a late answer after Cancel is discarded
                else:
                    self.last_result, self.last_error = outcome, None
                    self.changed.emit()
                    on_done(outcome)
                    return
            err = outcome if isinstance(outcome, AIError) else AIError(ErrorKind.UNKNOWN)
            self.last_error = err
            self.changed.emit()
            on_fail(err)

        def timed_out(msg: str) -> None:
            handle.cancel()                              # close the socket so the worker is freed, not left running
            finish(AIError(ErrorKind.TIMEOUT, msg))

        self.jobs.submit(work, finish, timed_out,
                         name="AI request", timeout_s=budget, key="ai-request")

    def cancel(self) -> None:
        h = self._handle
        if h is not None and self._active:
            self._cancelling = True
            self.activity.emit("cancelling")
            h.cancel()

    def test_connection(self, provider_id: str, on_done: Callable[[ConnectionResult], None]) -> None:
        """Contact the provider's read-only endpoint. No events, findings, projects or conversation text are sent."""
        if not self.enabled:
            QTimer.singleShot(0, lambda: on_done(ConnectionResult(False, provider_id, OFF_MESSAGE, "disabled")))
            return
        cfg = self.config()
        handle = CancelHandle()

        def work(_c, _p):
            return self.router.test_connection(provider_id, handle)

        def done(res: ConnectionResult) -> None:
            self.changed.emit()
            on_done(res)

        def fail(msg: str) -> None:
            self.changed.emit()
            on_done(ConnectionResult(False, provider_id, msg, "timeout"))

        self.jobs.submit(work, done, fail, name=f"AI test {provider_id}",
                         timeout_s=cfg.provider(provider_id).timeout_s + 5, key=f"ai-test-{provider_id}")

    def test_proxy(self, on_done: Callable[[ConnectionResult], None]) -> None:
        """Check the configured proxy: tunnel to the primary provider's host and complete the TLS handshake only."""
        if not self.enabled:
            QTimer.singleShot(0, lambda: on_done(ConnectionResult(False, "proxy", OFF_MESSAGE, "disabled")))
            return
        cfg = self.config()
        handle = CancelHandle()

        def work(_c, _p):
            try:
                return self.router.test_proxy(handle)
            except AIError as exc:                       # proxy enabled but unusable
                return ConnectionResult(False, "proxy", exc.message, exc.kind.value)

        def done(res: ConnectionResult) -> None:
            self.changed.emit()
            on_done(res)

        def fail(msg: str) -> None:
            handle.cancel()
            on_done(ConnectionResult(False, "proxy", msg, "timeout"))

        self.jobs.submit(work, done, fail, name="AI proxy test",
                         timeout_s=cfg.provider(cfg.primary).timeout_s + 5, key="ai-test-proxy")

    def list_models(self, provider_id: str, on_done: Callable[[list], None], on_fail: Callable[[str], None]) -> None:
        if not self.enabled:
            QTimer.singleShot(0, lambda: on_fail(OFF_MESSAGE))
            return
        cfg = self.config()
        handle = CancelHandle()

        def work(_c, _p):
            try:
                return self.router.list_models(provider_id, handle)
            except AIError as exc:
                return exc

        def done(res: Any) -> None:
            on_fail(res.message) if isinstance(res, AIError) else on_done(res)

        self.jobs.submit(work, done, on_fail, name=f"AI models {provider_id}",
                         timeout_s=cfg.provider(provider_id).timeout_s + 5, key=f"ai-models-{provider_id}")

    # ------------------------------------------------------------------ diagnostics
    def status_snapshot(self) -> dict:
        snap = self.usage.snapshot()
        snap.update(enabled=self.enabled, queue_depth=self.jobs.pending_count, active=1 if self._active else 0,
                    workers=self.jobs.max_threads, secure_storage=self.secrets.storage_available,
                    health={p: self.health(p).value for p in PROVIDER_IDS})
        return snap

    def shutdown(self) -> None:
        self._closed = True
        h = self._handle
        if h is not None:
            h.cancel()
        self.jobs.shutdown(2000)
