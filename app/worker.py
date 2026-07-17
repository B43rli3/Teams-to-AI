"""Asynchroner Polling-Worker für Teams-Nachrichten."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from app.attachments import AttachmentProcessor
from app.config import Settings
from app.exceptions import (
    GraphAPIError,
    GraphPermissionError,
    OllamaContextTooLargeError,
    OllamaError,
)
from app.language_guard import looks_predominantly_english
from app.llm_client import OllamaClient
from app.llm_prompts import GERMAN_RETRY_PROMPT, build_system_prompt
from app.logging_config import get_logger, truncate_id, truncate_text
from app.message_parser import MessageParser
from app.pdf_export import default_pdf_filename, generate_pdf_from_text
from app.reply_intent import wants_pdf_attachment
from app.repository import Repository
from app.teams_attachments import append_attachment_markup
from app.teams_service import TeamsMessage, TeamsService
from app.teams_targets import TeamsTarget

logger = get_logger(__name__)


class PollingWorker:
    """Pollt Microsoft Graph und verarbeitet neue Teams-Nachrichten."""

    def __init__(
        self,
        settings: Settings,
        teams_service: TeamsService,
        ollama_client: OllamaClient,
        repository: Repository,
        message_parser: MessageParser,
        attachment_processor: AttachmentProcessor | None = None,
    ) -> None:
        self._settings = settings
        self._teams = teams_service
        self._ollama = ollama_client
        self._repo = repository
        self._parser = message_parser
        self._attachments = attachment_processor
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._poll_lock = asyncio.Lock()
        self._llm_semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
        self._last_successful_poll: str | None = None
        self._last_poll_error: str | None = None
        self._permission_error = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_successful_poll(self) -> str | None:
        return self._last_successful_poll

    @property
    def last_poll_error(self) -> str | None:
        return self._last_poll_error

    async def start(self) -> None:
        """Startet den Polling-Worker als Hintergrundtask."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        if not self._settings.process_backlog:
            abandoned = await self._repo.abandon_queued_messages()
            if abandoned:
                logger.info(
                    "startup_backlog_cleared",
                    abandoned=abandoned,
                    hint="PROCESS_BACKLOG=false: offene queued Nachrichten verworfen.",
                )
        logger.info(
            "polling_worker_started",
            interval=self._settings.poll_interval_seconds,
            targets=len(self._teams.get_targets()),
        )

    async def stop(self) -> None:
        """Stoppt den Polling-Worker sauber."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("polling_worker_stopped")

    async def poll_now(self) -> int:
        """Löst einen sofortigen Poll aus. Gibt Anzahl neuer Nachrichten zurück."""
        async with self._poll_lock:
            return await self._do_poll()

    async def _poll_loop(self) -> None:
        """Hauptschleife für periodisches Polling."""
        while self._running:
            try:
                async with self._poll_lock:
                    await self._do_poll()
            except GraphPermissionError as exc:
                self._permission_error = True
                self._last_poll_error = str(exc)
                logger.error("graph_permission_error", error=str(exc))
                await asyncio.sleep(60)
            except Exception as exc:
                self._last_poll_error = str(exc)[:200]
                logger.error("poll_loop_error", error=str(exc)[:200])

            if self._permission_error:
                await asyncio.sleep(60)
            else:
                await asyncio.sleep(self._settings.poll_interval_seconds)

    async def _do_poll(self) -> int:
        """Führt einen einzelnen Poll-Zyklus über alle Ziele aus."""
        if self._permission_error:
            return 0

        try:
            total_new = 0
            total_fetched = 0
            for target in self._teams.get_targets():
                messages = await self._teams.fetch_messages_for_target(target)
                total_fetched += len(messages)
                total_new += await self._process_polled_messages(
                    messages, target_key=target.key
                )

            self._last_successful_poll = datetime.now(UTC).isoformat()
            self._last_poll_error = None
            logger.info(
                "poll_completed",
                new_messages=total_new,
                total_fetched=total_fetched,
                targets=len(self._teams.get_targets()),
            )
            await self._process_queued_messages()
            return total_new
        except GraphPermissionError:
            raise
        except GraphAPIError as exc:
            self._last_poll_error = str(exc)[:200]
            logger.error("poll_graph_error", error=str(exc)[:200])
            raise
        except Exception as exc:
            self._last_poll_error = str(exc)[:200]
            logger.error("poll_error", error=str(exc)[:200])
            raise

    async def _process_polled_messages(
        self,
        messages: list[TeamsMessage],
        *,
        target_key: str,
    ) -> int:
        """Verarbeitet abgerufene Nachrichten eines Ziels."""
        initial_poll_done = await self._repo.is_initial_poll_done(target_key=target_key)
        new_messages = 0

        unknown_messages: list[TeamsMessage] = []
        for message in messages:
            known = await self._repo.is_message_known(
                message.id, target_key=target_key
            )
            if not known:
                unknown_messages.append(message)

        if not initial_poll_done and not self._settings.process_backlog:
            for message in unknown_messages:
                await self._repo.insert_message(
                    message_id=message.id,
                    root_message_id=message.root_message_id,
                    created_at=message.created_at,
                    sender_id=message.sender_id,
                    sender_name=message.sender_name,
                    status="seen",
                    target_key=target_key,
                )
            await self._repo.mark_initial_poll_done(target_key=target_key)
            logger.info(
                "initial_poll_backlog_skipped",
                marked_seen=len(unknown_messages),
                target_key=truncate_id(target_key, 40),
            )
            return 0

        if not initial_poll_done and self._settings.process_backlog:
            backlog_messages = unknown_messages[: self._settings.backlog_limit]
            for message in backlog_messages:
                should_process, reason = self._teams.should_process_message(
                    message, already_processed=False
                )
                status = "queued" if should_process else "seen"
                inserted = await self._repo.insert_message(
                    message_id=message.id,
                    root_message_id=message.root_message_id,
                    created_at=message.created_at,
                    sender_id=message.sender_id,
                    sender_name=message.sender_name,
                    status=status,
                    target_key=target_key,
                )
                if inserted and should_process:
                    new_messages += 1
                elif not should_process:
                    logger.debug(
                        "message_ignored",
                        message_id=truncate_id(message.id),
                        reason=reason,
                        target_key=truncate_id(target_key, 40),
                    )

            for message in unknown_messages[self._settings.backlog_limit :]:
                await self._repo.insert_message(
                    message_id=message.id,
                    root_message_id=message.root_message_id,
                    created_at=message.created_at,
                    sender_id=message.sender_id,
                    sender_name=message.sender_name,
                    status="seen",
                    target_key=target_key,
                )

            await self._repo.mark_initial_poll_done(target_key=target_key)
        else:
            for message in unknown_messages:
                should_process, reason = self._teams.should_process_message(
                    message, already_processed=False
                )
                status = "queued" if should_process else "ignored"
                if not should_process and reason in ("own_message", "already_processed"):
                    status = "ignored"
                elif not should_process:
                    status = "seen"

                inserted = await self._repo.insert_message(
                    message_id=message.id,
                    root_message_id=message.root_message_id,
                    created_at=message.created_at,
                    sender_id=message.sender_id,
                    sender_name=message.sender_name,
                    status=status,
                    target_key=target_key,
                )
                if inserted and should_process:
                    new_messages += 1
                elif not should_process:
                    logger.debug(
                        "message_ignored",
                        message_id=truncate_id(message.id),
                        reason=reason,
                        target_key=truncate_id(target_key, 40),
                    )

        return new_messages

    async def _process_queued_messages(self) -> None:
        """Verarbeitet alle queued Nachrichten."""
        queued = await self._repo.get_queued_messages()
        for item in queued:
            await self._process_single_message(
                item["message_id"],
                target_key=item["target_key"],
            )

    def _find_target(self, target_key: str) -> TeamsTarget | None:
        for target in self._teams.get_targets():
            if target.key == target_key:
                return target
        return None

    async def _process_single_message(
        self,
        message_id: str,
        *,
        target_key: str = "",
    ) -> None:
        """Verarbeitet eine einzelne Nachricht."""
        claimed = await self._repo.try_claim_message(
            message_id, target_key=target_key
        )
        if not claimed:
            return

        target = self._find_target(target_key)
        if target is None and target_key:
            await self._repo.update_message_failed(
                message_id,
                f"Ziel nicht mehr konfiguriert: {target_key}",
                target_key=target_key,
            )
            return

        async with self._llm_semaphore:
            try:
                if target is not None:
                    messages = await self._teams.fetch_messages_for_target(target)
                else:
                    messages = await self._teams.fetch_channel_messages()
                target_msg = next((m for m in messages if m.id == message_id), None)

                if target_msg is None:
                    await self._repo.update_message_failed(
                        message_id,
                        "Nachricht nicht mehr gefunden.",
                        target_key=target_key,
                    )
                    return

                status = await self._repo.get_message_status(
                    message_id, target_key=target_key
                )
                if status == "completed":
                    return

                clean_text = self._teams.extract_clean_text(target_msg)
                if clean_text is None:
                    await self._repo.update_message_failed(
                        message_id,
                        "Kein verwertbarer Textinhalt.",
                        target_key=target_key,
                    )
                    return

                effective_target = target_msg.target or target
                attachment_bundle = None
                images: list[str] = []
                if self._settings.process_attachments and self._attachments is not None:
                    attachment_bundle = await self._attachments.process_message_attachments(
                        message_id=target_msg.id,
                        body_html=target_msg.body_content,
                        attachments=target_msg.attachments,
                        target=effective_target,
                    )
                    images = attachment_bundle.images_base64

                user_content = clean_text.strip()
                if attachment_bundle is not None:
                    context_block = attachment_bundle.build_context_block()
                    if context_block:
                        if user_content:
                            user_content = f"{user_content}\n\n{context_block}"
                        else:
                            user_content = (
                                "Bitte analysiere die angehängten Inhalte.\n\n"
                                f"{context_block}"
                            )

                if not user_content.strip() and not images:
                    await self._repo.update_message_failed(
                        message_id,
                        "Kein Text und keine verwertbaren Anhänge.",
                        target_key=target_key,
                    )
                    return

                context = await self._repo.get_conversation_messages(
                    target_msg.root_message_id,
                    limit=self._settings.llm_max_context_messages,
                    target_key=target_key,
                )

                llm_messages: list[dict[str, str]] = []
                for ctx_msg in context:
                    llm_messages.append(
                        {"role": ctx_msg["role"], "content": ctx_msg["content"]}
                    )
                llm_messages.append(
                    {
                        "role": "user",
                        "content": user_content
                        or "Bitte analysiere die angehängten Bilder.",
                    }
                )

                wants_pdf = (
                    self._settings.send_pdf_replies
                    and wants_pdf_attachment(user_content)
                )

                system_prompt = build_system_prompt(
                    self._settings.llm_system_prompt,
                    include_image_hint=bool(images),
                    include_pdf_hint=wants_pdf,
                )

                try:
                    llm_response = await self._ollama.chat(
                        llm_messages,
                        system_prompt=system_prompt,
                        images=images or None,
                    )
                except OllamaContextTooLargeError:
                    logger.warning(
                        "ollama_context_too_large_retry",
                        message_id=truncate_id(message_id),
                        had_images=bool(images),
                    )
                    # Retry ohne Bilder und mit gekürztem Kontext
                    truncated_user = truncate_text(user_content, 6000)
                    compact_messages = [
                        {
                            "role": "user",
                            "content": (
                                truncated_user
                                or "Bitte gib eine kurze Antwort auf Deutsch."
                            ),
                        }
                    ]
                    llm_response = await self._ollama.chat(
                        compact_messages,
                        system_prompt=system_prompt,
                        images=None,
                    )
                    images = []

                if self._settings.llm_force_german_retry and looks_predominantly_english(
                    llm_response
                ):
                    logger.info("llm_german_retry_triggered", message_id=truncate_id(message_id))
                    llm_messages.append({"role": "assistant", "content": llm_response})
                    llm_messages.append({"role": "user", "content": GERMAN_RETRY_PROMPT})
                    try:
                        llm_response = await self._ollama.chat(
                            llm_messages,
                            system_prompt=system_prompt,
                            images=images or None,
                        )
                    except OllamaContextTooLargeError:
                        llm_response = await self._ollama.chat(
                            [
                                {
                                    "role": "user",
                                    "content": (
                                        f"{GERMAN_RETRY_PROMPT}\n\n"
                                        f"Vorherige Antwort:\n{llm_response[:4000]}"
                                    ),
                                }
                            ],
                            system_prompt=system_prompt,
                            images=None,
                        )

                html_response = self._parser.format_llm_response_for_teams(llm_response)
                attachments: list[dict[str, str]] | None = None

                if wants_pdf:
                    try:
                        pdf_bytes = generate_pdf_from_text(
                            title="KI-Antwort",
                            body=llm_response,
                        )
                        pdf_name = default_pdf_filename(message_id=message_id)
                        attachment = await self._teams.upload_pdf_reply(
                            filename=pdf_name,
                            pdf_bytes=pdf_bytes,
                            target=effective_target,
                        )
                        attachments = [attachment]
                        html_response = append_attachment_markup(
                            html_response,
                            attachment["id"],
                        )
                        html_response = (
                            f"{html_response}<p><em>PDF-Anhang: {attachment['name']}</em></p>"
                        )
                        logger.info(
                            "pdf_reply_prepared",
                            message_id=truncate_id(message_id),
                            filename=attachment["name"],
                            bytes=len(pdf_bytes),
                        )
                    except Exception as exc:
                        logger.warning(
                            "pdf_reply_failed",
                            message_id=truncate_id(message_id),
                            error=str(exc)[:200],
                        )
                        html_response = (
                            f"{html_response}<p><em>"
                            "[PDF konnte nicht erstellt oder hochgeladen werden: "
                            f"{truncate_text(str(exc), 120)}]"
                            "</em></p>"
                        )

                reply_id = await self._teams.send_thread_reply(
                    target_msg.root_message_id,
                    html_response,
                    attachments=attachments,
                    target=effective_target,
                )

                await self._repo.add_conversation_message(
                    target_msg.root_message_id,
                    "user",
                    user_content or "[Bildanhang]",
                    target_key=target_key,
                )
                await self._repo.add_conversation_message(
                    target_msg.root_message_id,
                    "assistant",
                    llm_response,
                    target_key=target_key,
                )
                await self._repo.update_message_completed(
                    message_id, reply_id, target_key=target_key
                )

                logger.info(
                    "message_processed",
                    message_id=truncate_id(message_id),
                    preview=truncate_text(user_content or "[bild]"),
                    images=len(images),
                    target_key=truncate_id(target_key, 40),
                )

            except OllamaError as exc:
                await self._repo.update_message_failed(
                    message_id, f"Ollama-Fehler: {exc}", target_key=target_key
                )
                logger.error(
                    "ollama_processing_error",
                    message_id=truncate_id(message_id),
                    error=str(exc)[:200],
                )
            except GraphAPIError as exc:
                await self._repo.update_message_failed(
                    message_id, f"Graph-Fehler: {exc}", target_key=target_key
                )
                logger.error(
                    "graph_processing_error",
                    message_id=truncate_id(message_id),
                    error=str(exc)[:200],
                )
            except Exception as exc:
                await self._repo.update_message_failed(
                    message_id, f"Verarbeitungsfehler: {exc}", target_key=target_key
                )
                logger.error(
                    "processing_error",
                    message_id=truncate_id(message_id),
                    error=str(exc)[:200],
                )
