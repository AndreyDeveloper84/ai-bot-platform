"""Persistent MCP-клиент для maxbot.

Один subprocess `formulatela_mcp` живёт всё время работы maxbot-процесса.
Это критично для производительности: spawn нового subprocess'а каждый раз
~500ms (см. docs/plans/maxbot-phase2-research-T01.md §1.2).

Жизненный цикл:
1. `build_dispatcher()` создаёт `MaxbotMCPClient` singleton
2. При первом `await client.ensure_started()` — spawn subprocess + initialize
   ClientSession + list_tools кэширует
3. handlers вызывают `await client.call_tool(name, args)` многократно
4. При shutdown maxbot — `await client.close()` (закрывает session+subprocess)

Singleton — потому что в FastMCP сейчас только stdio (один subprocess на
коннект), и держать N сессий = N subprocess'ов = бессмысленно. При
переходе на streamable-http (Фаза 2.5) можно расширить.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


logger = logging.getLogger("maxbot.mcp_client")


class MaxbotMCPClient:
    """Singleton-клиент для persistent stdio-сессии с formulatela_mcp."""

    _instance: "MaxbotMCPClient | None" = None

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()
        self._tools_cache: list[Any] | None = None

    @classmethod
    def instance(cls) -> "MaxbotMCPClient":
        """Singleton accessor — один client на весь процесс."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """ТОЛЬКО для тестов — сбросить singleton между ними."""
        cls._instance = None

    async def ensure_started(self) -> None:
        """Идемпотентный старт. Безопасно вызывать на каждый handler-call."""
        if self._session is not None:
            return
        async with self._lock:
            if self._session is not None:  # double-check после lock
                return
            await self._start()

    async def _start(self) -> None:
        """Spawn MCP subprocess + initialize session. Под lock."""
        params = self._build_server_params()
        logger.info("Starting MCP subprocess: %s %s", params.command, " ".join(params.args))

        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools_resp = await session.list_tools()
            self._tools_cache = list(tools_resp.tools)
            logger.info("MCP subprocess ready, tools: %s",
                        [t.name for t in self._tools_cache])
            self._session = session
            self._stack = stack
        except Exception:
            await stack.aclose()
            raise

    def _build_server_params(self) -> StdioServerParameters:
        """Параметры spawn'а MCP subprocess.

        Override через env:
        - MCP_PYTHON: путь к python (по умолчанию sys.executable)
        - MCP_MODULE:  -m аргумент (по умолчанию formulatela_mcp.main)
        - PYTHONPATH добавляется для доступа к mysite.settings из subprocess
        """
        python = os.environ.get("MCP_PYTHON", sys.executable)
        module = os.environ.get("MCP_MODULE", "formulatela_mcp.main")

        # Пробросить current env + добавить PYTHONPATH к mysite/
        env = dict(os.environ)
        # PYTHONPATH должен включать корень Django (где manage.py)
        # Локально это .../mysite/, на проде .../formula_tela/mysite/
        # Если уже есть в env — не перезаписываем
        if "PYTHONPATH" not in env or "mysite" not in env["PYTHONPATH"]:
            mysite_root = Path(__file__).resolve().parent.parent  # maxbot/.. = mysite/
            env["PYTHONPATH"] = str(mysite_root) + os.pathsep + env.get("PYTHONPATH", "")

        return StdioServerParameters(
            command=python,
            args=["-m", module],
            env=env,
        )

    def list_tools(self) -> list[Any]:
        """Список tool'ов из MCP-сервера (кэшировано после initialize)."""
        if self._tools_cache is None:
            raise RuntimeError("MaxbotMCPClient.ensure_started() ещё не вызван")
        return self._tools_cache

    async def call_tool(self, name: str, args: dict) -> Any:
        """Вызвать MCP-tool. Возвращает CallToolResult.

        НЕ парсит content — caller сам делает json.loads(result.content[0].text)
        для tool'ов которые возвращают JSON.
        """
        await self.ensure_started()
        assert self._session is not None
        return await self._session.call_tool(name, args)

    async def close(self) -> None:
        """Graceful shutdown — закрывает session + terminate subprocess."""
        if self._stack is None:
            return
        async with self._lock:
            if self._stack is None:
                return
            try:
                await self._stack.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP close exception (ignored): %r", exc)
            finally:
                self._session = None
                self._stack = None
                self._tools_cache = None
