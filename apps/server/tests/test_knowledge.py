from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.db import repo
from agent_factory_server.domain.schemas import (
    AgentCreate,
    KbDocumentUpdate,
    KnowledgePolicy,
)
from agent_factory_server.runtime.knowledge import (
    KB_LIST_TOOL_NAME,
    KB_PAGE_CHARS,
    KB_READ_TOOL_NAME,
    execute_knowledge_tool,
    format_knowledge_catalog,
    load_knowledge_block,
)
from agent_factory_server.runtime.knowledge_summary import extractive_summary

AGENT = "agent-1"
OTHER_AGENT = "agent-2"


class KnowledgeBaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "kb.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    async def _seed(self, session: AsyncSession) -> None:
        await repo.upsert_kb_document(
            session,
            agent_id=AGENT,
            scope="agent",
            path="售后/退货政策.md",
            title="退货政策",
            text="七天无理由退货，运费由买家承担。" * 600,
            summary="七天无理由退货条件与运费承担",
        )
        await repo.upsert_kb_document(
            session,
            agent_id=AGENT,
            scope="agent",
            path="售后/发票/电子发票.md",
            title="电子发票",
            text="订单详情页自助申请，3 个工作日开具。",
            summary="电子发票申请路径与时效",
        )
        await repo.upsert_kb_document(
            session,
            agent_id=AGENT,
            scope="user",
            user_key="u1",
            path="我的合同/合同A.md",
            title="合同A",
            text="用户 U1 的合同正文。",
            summary="U1 的合同",
        )
        for index in range(10):
            await repo.upsert_kb_document(
                session,
                agent_id=AGENT,
                scope="agent",
                path=f"售后/条款{index:02d}.md",
                title=f"条款{index:02d}",
                text="条款正文。",
                summary="这是一条用于压测目录预算的较长摘要文字",
            )
        await repo.upsert_kb_document(
            session,
            agent_id=OTHER_AGENT,
            scope="agent",
            path="别的Agent专属.md",
            title="别的 Agent",
            text="不应出现在 agent-1 的上下文里。",
            summary="另一个 Agent 的私有文档",
        )

    async def test_catalog_degrades_under_budget(self):
        async with self.Session() as session:
            await self._seed(session)
            docs = await repo.list_visible_kb_documents(
                session, agent_id=AGENT, user_key="u1"
            )
            self.assertEqual(len(docs), 13)

            full = format_knowledge_catalog(docs, max_chars=6000)
            self.assertIn("七天无理由退货条件与运费承担", full)
            self.assertIn("售后/退货政策.md", full)

            # Summaries are dropped first; every path still fits.
            paths_only = format_knowledge_catalog(docs, max_chars=450)
            self.assertNotIn("七天无理由退货条件与运费承担", paths_only)
            self.assertNotIn("压测目录预算", paths_only)
            self.assertIn("售后/退货政策.md", paths_only)
            self.assertLessEqual(len(paths_only), 450)

            # Too many paths: collapse into folders with doc counts.
            collapsed = format_knowledge_catalog(docs, max_chars=300)
            self.assertIn("折叠", collapsed)
            self.assertIn("售后/", collapsed)
            self.assertLessEqual(len(collapsed), 300)

    async def test_catalog_without_docs_is_empty(self):
        self.assertEqual(format_knowledge_catalog([], max_chars=1000), "")

    async def test_block_is_scoped_to_agent_and_user(self):
        async with self.Session() as session:
            await self._seed(session)

            # Agent-shared docs + this user's docs of the same Agent.
            text, count = await load_knowledge_block(
                session, agent_id=AGENT, user_key="u1", policy=KnowledgePolicy()
            )
            self.assertEqual(count, 13)
            self.assertIn("合同A", text)
            self.assertNotIn("别的Agent专属", text)

            # Another Agent has its own corpus and its own user docs: 合同A belongs
            # to agent-1, so it must not appear here even with the same user_key.
            text2, count2 = await load_knowledge_block(
                session, agent_id=OTHER_AGENT, user_key="u1", policy=KnowledgePolicy()
            )
            self.assertEqual(count2, 1)
            self.assertNotIn("退货政策", text2)
            self.assertNotIn("合同A", text2)
            self.assertIn("别的Agent专属", text2)

            # Another user of the same Agent sees only the shared corpus.
            text3, count3 = await load_knowledge_block(
                session, agent_id=AGENT, user_key="u2", policy=KnowledgePolicy()
            )
            self.assertEqual(count3, 12)
            self.assertNotIn("合同A", text3)

            # Opt out of personal documents.
            text4, count4 = await load_knowledge_block(
                session,
                agent_id=AGENT,
                user_key="u1",
                policy=KnowledgePolicy(include_user_docs=False),
            )
            self.assertEqual(count4, 12)
            self.assertNotIn("合同A", text4)

    async def test_kb_list_browses_tree(self):
        async with self.Session() as session:
            await self._seed(session)
            policy = KnowledgePolicy()
            root = await execute_knowledge_tool(
                session,
                agent_id=AGENT,
                name=KB_LIST_TOOL_NAME,
                arguments={},
                user_key="u1",
                policy=policy,
            )
            self.assertTrue(root["ok"])
            folders = {e["path"] for e in root["data"]["entries"] if e["type"] == "folder"}
            self.assertIn("售后", folders)
            self.assertIn("我的合同", folders)

            nested = await execute_knowledge_tool(
                session,
                agent_id=AGENT,
                name=KB_LIST_TOOL_NAME,
                arguments={"path": "售后"},
                user_key="u1",
                policy=policy,
            )
            names = {e.get("path") for e in nested["data"]["entries"]}
            self.assertIn("售后/退货政策.md", names)
            self.assertIn("售后/发票", names)

            # Another agent sees a different tree.
            other = await execute_knowledge_tool(
                session,
                agent_id=OTHER_AGENT,
                name=KB_LIST_TOOL_NAME,
                arguments={},
                user_key="u1",
                policy=policy,
            )
            other_names = {e.get("path") for e in other["data"]["entries"]}
            self.assertEqual(other_names, {"别的Agent专属.md"})

    async def test_kb_read_pages_and_isolates_owners(self):
        async with self.Session() as session:
            await self._seed(session)
            policy = KnowledgePolicy()
            listing = await execute_knowledge_tool(
                session,
                agent_id=AGENT,
                name=KB_LIST_TOOL_NAME,
                arguments={"path": "售后"},
                user_key="u1",
                policy=policy,
            )
            doc_id = next(
                e["doc_id"]
                for e in listing["data"]["entries"]
                if e["type"] == "doc" and e["path"].endswith("退货政策.md")
            )
            first = await execute_knowledge_tool(
                session,
                agent_id=AGENT,
                name=KB_READ_TOOL_NAME,
                arguments={"doc_id": doc_id},
                user_key="u1",
                policy=policy,
            )
            self.assertTrue(first["ok"])
            self.assertLessEqual(len(first["data"]["content"]), KB_PAGE_CHARS)
            self.assertIsNotNone(first["data"]["next_offset"])

            second = await execute_knowledge_tool(
                session,
                agent_id=AGENT,
                name=KB_READ_TOOL_NAME,
                arguments={"doc_id": doc_id, "offset": first["data"]["next_offset"]},
                user_key="u1",
                policy=policy,
            )
            self.assertTrue(second["ok"])
            self.assertEqual(second["data"]["offset"], first["data"]["next_offset"])

            # Another agent cannot read agent-1's doc even with the same user_key.
            denied_agent = await execute_knowledge_tool(
                session,
                agent_id=OTHER_AGENT,
                name=KB_READ_TOOL_NAME,
                arguments={"doc_id": doc_id},
                user_key="u1",
                policy=policy,
            )
            self.assertFalse(denied_agent["ok"])

            # Personal docs belong to one Agent: readable by their owner only.
            personal = await repo.list_kb_documents(
                session, agent_id=AGENT, scope="user", user_key="u1"
            )
            denied_user = await execute_knowledge_tool(
                session,
                agent_id=AGENT,
                name=KB_READ_TOOL_NAME,
                arguments={"doc_id": personal[0].id},
                user_key="u2",
                policy=policy,
            )
            self.assertFalse(denied_user["ok"])
            denied_other_agent = await execute_knowledge_tool(
                session,
                agent_id=OTHER_AGENT,
                name=KB_READ_TOOL_NAME,
                arguments={"doc_id": personal[0].id},
                user_key="u1",
                policy=policy,
            )
            self.assertFalse(denied_other_agent["ok"])
            allowed = await execute_knowledge_tool(
                session,
                agent_id=AGENT,
                name=KB_READ_TOOL_NAME,
                arguments={"doc_id": personal[0].id},
                user_key="u1",
                policy=policy,
            )
            self.assertTrue(allowed["ok"])

    async def test_folder_move_and_delete_rewrite_paths(self):
        async with self.Session() as session:
            await self._seed(session)
            moved = await repo.move_kb_folder(
                session,
                agent_id=AGENT,
                scope="agent",
                path="售后",
                new_path="客户服务",
            )
            self.assertEqual(moved, 12)
            docs = await repo.list_kb_documents(session, agent_id=AGENT, scope="agent")
            self.assertTrue(all(d.path.startswith("客户服务/") for d in docs))
            # Other agents are untouched.
            other = await repo.list_kb_documents(
                session, agent_id=OTHER_AGENT, scope="agent"
            )
            self.assertEqual([d.path for d in other], ["别的Agent专属.md"])

            deleted = await repo.delete_kb_folder(
                session,
                agent_id=AGENT,
                scope="agent",
                path="客户服务",
            )
            self.assertEqual(deleted, 12)
            self.assertEqual(
                await repo.list_kb_documents(session, agent_id=AGENT, scope="agent"),
                [],
            )

    async def test_deleting_agent_removes_its_documents(self):
        async with self.Session() as session:
            agent = await repo.create_agent(
                session, AgentCreate(name="临时 Agent", system_prompt="x")
            )
            await repo.upsert_kb_document(
                session,
                agent_id=agent.id,
                scope="agent",
                path="a.md",
                text="正文",
                summary="摘要",
            )
            await repo.upsert_kb_document(
                session,
                agent_id=agent.id,
                scope="user",
                user_key="u1",
                path="b.md",
                text="正文",
                summary="摘要",
            )
            elsewhere, _ = await repo.upsert_kb_document(
                session,
                agent_id=AGENT,
                scope="agent",
                path="keep.md",
                text="正文",
                summary="摘要",
            )

            self.assertTrue(await repo.delete_agent(session, agent.id))
            # 文档严格归属 Agent：删 Agent 不能留下谁都看不到的孤儿文档。
            self.assertEqual(
                await repo.list_kb_documents(
                    session, agent_id=agent.id, scope="agent", include_disabled=True
                ),
                [],
            )
            self.assertEqual(
                await repo.list_kb_documents(
                    session,
                    agent_id=agent.id,
                    scope="user",
                    user_key="u1",
                    include_disabled=True,
                ),
                [],
            )
            kept = await repo.list_kb_documents(session, agent_id=AGENT, scope="agent")
            self.assertEqual([d.id for d in kept], [elsewhere.id])

    async def test_page_lists_all_users_and_paginates(self):
        async with self.Session() as session:
            await self._seed(session)
            await repo.upsert_kb_document(
                session,
                agent_id=AGENT,
                scope="user",
                user_key="u2",
                path="我的合同/合同B.md",
                text="用户 U2 的合同正文。",
                summary="U2 的合同",
            )

            # Personal scope without user_key → all end users of this Agent.
            items, total, user_keys = await repo.page_kb_documents(
                session, agent_id=AGENT, scope="user"
            )
            self.assertEqual(total, 2)
            self.assertEqual({d.user_key for d in items}, {"u1", "u2"})
            self.assertEqual(user_keys, ["u1", "u2"])

            # Page size is enforced and total stays the unpaged count.
            first, total2, _ = await repo.page_kb_documents(
                session, agent_id=AGENT, scope="agent", page=1, page_size=3
            )
            self.assertEqual(total2, 12)
            self.assertEqual(len(first), 3)
            second, _, _ = await repo.page_kb_documents(
                session, agent_id=AGENT, scope="agent", page=2, page_size=3
            )
            self.assertTrue({d.id for d in first}.isdisjoint({d.id for d in second}))

            # keyword filters on path/title/summary.
            hits, hit_total, _ = await repo.page_kb_documents(
                session, agent_id=AGENT, scope="agent", keyword="电子发票"
            )
            self.assertEqual(hit_total, 1)
            self.assertEqual(hits[0].path, "售后/发票/电子发票.md")

            # path_prefix narrows to one folder subtree.
            nested, nested_total, _ = await repo.page_kb_documents(
                session, agent_id=AGENT, scope="agent", path_prefix="售后/发票"
            )
            self.assertEqual(nested_total, 1)
            self.assertEqual(nested[0].path, "售后/发票/电子发票.md")

            # Another Agent sees none of it.
            _, other_total, other_keys = await repo.page_kb_documents(
                session, agent_id=OTHER_AGENT, scope="user"
            )
            self.assertEqual(other_total, 0)
            self.assertEqual(other_keys, [])

    async def test_documents_always_belong_to_an_agent(self):
        async with self.Session() as session:
            # No Agent → rejected.
            with self.assertRaises(ValueError):
                await repo.upsert_kb_document(
                    session, agent_id="", scope="agent", path="x.md", text="x"
                )
            # User docs still need a user_key.
            with self.assertRaises(ValueError):
                await repo.upsert_kb_document(
                    session, agent_id=AGENT, scope="user", user_key="", path="x.md", text="x"
                )
            # No other top-level scope exists.
            with self.assertRaises(ValueError):
                await repo.upsert_kb_document(
                    session, agent_id=AGENT, scope="global", path="x.md", text="x"
                )
            with self.assertRaises(ValueError):
                await repo.list_kb_documents(session, agent_id="", scope="agent")

    async def test_upsert_replaces_same_path_and_rejects_escapes(self):
        async with self.Session() as session:
            first, created = await repo.upsert_kb_document(
                session, agent_id=AGENT, scope="agent", path="a/b.md", text="v1", summary="s1"
            )
            self.assertTrue(created)
            second, created2 = await repo.upsert_kb_document(
                session, agent_id=AGENT, scope="agent", path="a/b.md", text="v2", summary="s2"
            )
            self.assertFalse(created2)
            self.assertEqual(first.id, second.id)
            self.assertEqual(second.summary, "s2")

            # Same path under another agent is a separate document.
            other, created3 = await repo.upsert_kb_document(
                session,
                agent_id=OTHER_AGENT,
                scope="agent",
                path="a/b.md",
                text="v3",
                summary="s3",
            )
            self.assertTrue(created3)
            self.assertNotEqual(other.id, second.id)

            with self.assertRaises(ValueError):
                await repo.upsert_kb_document(
                    session, agent_id=AGENT, scope="agent", path="../escape.md", text="x"
                )

    async def test_path_collisions_are_rejected(self):
        async with self.Session() as session:
            await self._seed(session)
            target = await repo.find_kb_document_by_path(
                session, agent_id=AGENT, scope="agent", path="售后/退货政策.md"
            )
            assert target is not None

            with self.assertRaises(ValueError):
                await repo.update_kb_document(
                    session,
                    target.id,
                    KbDocumentUpdate(path="售后/发票/电子发票.md"),
                )

            # Moving a folder onto an existing file path is a conflict, not a crash.
            await repo.upsert_kb_document(
                session,
                agent_id=AGENT,
                scope="agent",
                path="归档/退货政策.md",
                text="占位",
                summary="占位",
            )
            with self.assertRaises(ValueError):
                await repo.move_kb_folder(
                    session,
                    agent_id=AGENT,
                    scope="agent",
                    path="售后",
                    new_path="归档",
                )

    def test_extractive_summary_cleans_markdown(self):
        text = (
            "---\ntitle: x\n---\n"
            "## 退货政策\n\n"
            "```bash\nrm -rf /\n```\n\n"
            "自签收之日起七天内可无理由退货。运费由买家承担；定制商品除外。"
        )
        summary = extractive_summary(text, title="退货政策", max_chars=40)
        self.assertIn("七天", summary)
        self.assertNotIn("rm -rf", summary)
        self.assertNotIn("##", summary)
        self.assertLessEqual(len(summary), 41)

    def test_extractive_summary_falls_back_to_title(self):
        self.assertEqual(extractive_summary("", title="空文档"), "空文档")

    async def test_factory_summary_falls_back_without_model(self):
        from agent_factory_server.runtime.knowledge_summary import generate_summary

        async with self.Session() as session:
            summary, source = await generate_summary(
                session,
                title="退货政策",
                text="自签收之日起七天内可无理由退货。运费由买家承担。",
            )
            self.assertEqual(source, "auto")
            self.assertIn("七天", summary)


if __name__ == "__main__":
    unittest.main()
