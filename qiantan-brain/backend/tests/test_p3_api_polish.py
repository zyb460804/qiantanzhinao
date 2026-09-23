"""QA 黑盒 N8/N9/N10 接口规范性回归（P3 打磨项）。

对应 qa/miniprogram-qa-20260919/测试报告-2026-09-19.md：
  N8  GET /catalog/skus 的 page/page_size/order_by 此前全被忽略、恒返回全量
      → 分页与排序真实生效（page_size 上限 100，order_by 白名单，非法值 422），
      响应沿用项目 PaginatedResponse 惯例：data 仍为列表 + meta 分页元数据。
  N9  GET /catalog/skus/{id}/aliases 与 /price-history 对不存在（或跨商户）
      SKU 此前返回 200 空数组 → 统一 404，与主资源存在性语义一致。
  N10 POST /purchase/from-advice 显式提交空 items 此前误报 404「未找到可用的
      采购建议」→ 400「采购清单不能为空」（空提交 ≠ 资源不存在）；
      不带 items 键的「生成今日清单」路径行为不变。

注：N11（/reports/product-ranking 的 limit/metric 校验、/reports/monthly 与
/reports/weekly 的未来日期校验）由账务任务完成后移交补丁实现，见 TestN11RankingAndMonthly；
顺带补了 /skus/{id}/specs 的 N9 同类 404 语义。
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from tests.conftest import TEST_MERCHANT_ID

pytestmark = pytest.mark.asyncio

SECOND_MERCHANT_ID = "00000000-0000-0000-0000-000000000002"


async def _create_sku(client, name, **extra):
    """建一个活跃 SKU，返回 sku_id。"""
    payload = {"name": name, "canonical_unit": "斤"}
    payload.update(extra)
    res = await client.post("/api/v1/catalog/skus", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["data"]["sku_id"]


class TestSkuListPagination:
    """N8：/catalog/skus 分页与排序真实生效。"""

    async def test_default_params_normal_values_unaffected(self, client):
        """不带分页参数 → 行为向后兼容：data 仍是列表、单页装得下全部。"""
        for i in range(3):
            await _create_sku(client, f"默认参数商品{i}")
        res = await client.get("/api/v1/catalog/skus")
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 3
        # 分页元数据出现在 meta（项目 PaginatedResponse 惯例）
        assert body["meta"]["page"] == 1
        assert body["meta"]["limit"] == 20
        assert body["meta"]["total"] == 3
        # 单条结构不变
        assert {"sku_id", "name", "canonical_unit"} <= set(body["data"][0])

    async def test_pagination_slices_without_overlap(self, client):
        """page/page_size 生效：各页不重叠、并集完整、末页可为短页。"""
        names = [f"分页商品{i}" for i in range(5)]
        for name in names:
            await _create_sku(client, name)

        pages = []
        for page in (1, 2, 3):
            res = await client.get(f"/api/v1/catalog/skus?page={page}&page_size=2")
            assert res.status_code == 200
            meta = res.json()["meta"]
            assert meta["page"] == page
            assert meta["limit"] == 2
            assert meta["total"] == 5
            pages.append([s["name"] for s in res.json()["data"]])

        assert len(pages[0]) == 2 and len(pages[1]) == 2 and len(pages[2]) == 1
        all_names = pages[0] + pages[1] + pages[2]
        assert len(set(all_names)) == 5
        assert set(all_names) == set(names)

    async def test_page_beyond_total_returns_empty(self, client):
        await _create_sku(client, "只有一页的商品")
        res = await client.get("/api/v1/catalog/skus?page=99&page_size=20")
        assert res.status_code == 200
        assert res.json()["data"] == []
        assert res.json()["meta"]["total"] == 1

    async def test_param_lower_bound_rejected_422(self, client):
        """page=0 / page_size=0 → 422（Query 声明式校验）。"""
        assert (await client.get("/api/v1/catalog/skus?page=0")).status_code == 422
        assert (await client.get("/api/v1/catalog/skus?page_size=0")).status_code == 422

    async def test_page_size_capped_at_100(self, client):
        """page_size 超上限 → 422（QA N11 同款「参数下限/上限校验」诉求）。"""
        res = await client.get("/api/v1/catalog/skus?page_size=101")
        assert res.status_code == 422
        # 边界值 100 合法
        assert (await client.get("/api/v1/catalog/skus?page_size=100")).status_code == 200

    async def test_order_by_default_is_name(self, client):
        """默认排序保持旧行为（按 name）。"""
        for name in ("b萝卜", "a白菜", "c豆腐"):
            await _create_sku(client, name)
        res = await client.get("/api/v1/catalog/skus")
        names = [s["name"] for s in res.json()["data"]]
        assert names == sorted(names)

    async def test_order_by_price_whitelist(self, client):
        """白名单字段排序生效（含 None 值行不崩溃）。"""
        await _create_sku(client, "贵价商品", default_sale_price=5)
        await _create_sku(client, "低价商品", default_sale_price=1)
        await _create_sku(client, "中价商品", default_sale_price=3)
        await _create_sku(client, "无价商品")

        res = await client.get("/api/v1/catalog/skus?order_by=default_sale_price")
        assert res.status_code == 200
        prices = [s["default_sale_price"] for s in res.json()["data"]]
        priced = [p for p in prices if p is not None]
        assert priced == sorted(priced)

    async def test_order_by_invalid_value_422(self, client):
        """非法 order_by → 422 + 中文报错（白名单外一律拒绝）。"""
        res = await client.get("/api/v1/catalog/skus?order_by=1; drop table skus")
        assert res.status_code == 422
        assert "order_by" in res.json()["detail"]

    async def test_deactivated_sku_excluded_from_total(self, client):
        """分页 total 只统计活跃 SKU（软删行不占坑）。"""
        sku_id = await _create_sku(client, "将被停用的商品")
        await _create_sku(client, "保留的商品")
        await client.delete(f"/api/v1/catalog/skus/{sku_id}")

        res = await client.get("/api/v1/catalog/skus")
        assert res.json()["meta"]["total"] == 1


class TestSkuSubresource404:
    """N9：不存在 / 跨商户 SKU 的子资源 → 404，而非 200 空数组。"""

    async def test_aliases_missing_sku_404(self, client):
        res = await client.get(f"/api/v1/catalog/skus/{uuid.uuid4()}/aliases")
        assert res.status_code == 404
        assert res.json()["detail"] == "SKU不存在"

    async def test_price_history_missing_sku_404(self, client):
        res = await client.get(f"/api/v1/catalog/skus/{uuid.uuid4()}/price-history")
        assert res.status_code == 404
        assert res.json()["detail"] == "SKU不存在"

    async def test_cross_merchant_subresource_404(self, client):
        """跨商户 SKU 探测同样 404（旧行为返回 200 空数组，存在性信息泄露）。"""
        sku_id = await _create_sku(client, "别家看不到的商品")
        other = {"X-Test-Merchant-Id": SECOND_MERCHANT_ID}
        res_alias = await client.get(
            f"/api/v1/catalog/skus/{sku_id}/aliases", headers=other
        )
        res_hist = await client.get(
            f"/api/v1/catalog/skus/{sku_id}/price-history", headers=other
        )
        assert res_alias.status_code == 404
        assert res_hist.status_code == 404

    async def test_existing_sku_subresources_still_200(self, client):
        """正常路径不受影响：真实 SKU 的别名与价格历史照常返回。"""
        sku_id = await _create_sku(client, "正常商品", default_sale_price=5)
        await client.post(
            f"/api/v1/catalog/skus/{sku_id}/aliases", json={"alias": "西红柿"}
        )
        # 改价一次，产生一条价格历史
        put_res = await client.put(
            f"/api/v1/catalog/skus/{sku_id}", json={"default_sale_price": 6}
        )
        assert put_res.status_code == 200

        alias_res = await client.get(f"/api/v1/catalog/skus/{sku_id}/aliases")
        assert alias_res.status_code == 200
        assert any(a["alias"] == "西红柿" for a in alias_res.json()["data"])

        hist_res = await client.get(f"/api/v1/catalog/skus/{sku_id}/price-history")
        assert hist_res.status_code == 200
        history = hist_res.json()["data"]
        assert len(history) >= 1
        assert history[0]["old_price"] == 5.0
        assert history[0]["new_price"] == 6.0


class TestPurchaseEmptyItems:
    """N10：from-advice 显式空 items → 400，与「建议不存在」404 语义分离。"""

    async def test_explicit_empty_items_400(self, client):
        res = await client.post("/api/v1/purchase/from-advice", json={"items": []})
        assert res.status_code == 400
        assert "采购清单不能为空" in res.json()["detail"]

    async def test_empty_items_and_empty_ids_400(self, client):
        res = await client.post(
            "/api/v1/purchase/from-advice", json={"items": [], "recommendation_ids": []}
        )
        assert res.status_code == 400
        assert "采购清单不能为空" in res.json()["detail"]

    async def test_items_null_treated_as_absent(self, client):
        """items 显式 null 视为未提供 → 走建议加载路径（无建议 → 原 404 不变）。"""
        res = await client.post("/api/v1/purchase/from-advice", json={"items": None})
        assert res.status_code == 404
        assert "未找到可用的采购建议" in res.json()["detail"]

    async def test_no_items_key_no_recs_still_404(self, client):
        """「一键生成今日清单」（purchase.js 不带 items 键）行为保持不变。"""
        res = await client.post("/api/v1/purchase/from-advice", json={})
        assert res.status_code == 404
        assert "未找到可用的采购建议" in res.json()["detail"]

    async def test_empty_items_with_valid_recommendation_ids_still_works(
        self, client, db_session
    ):
        """items=[] 但显式指定了存在的建议 ID → 不算空提交，建议正常导入。"""
        from app.models.recommendation import Recommendation

        from tests.conftest import TEST_MERCHANT_ID, TEST_PRODUCT_ID

        async with db_session() as session:
            session.add(
                Recommendation(
                    merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                    product_id=TEST_PRODUCT_ID,
                    suggestion="建议采购白菜",
                    basis=[],
                    recommended_qty=20,
                    confidence=0.8,
                )
            )
            await session.commit()

        # 直接从 DB 取回刚插入的建议 ID
        async with db_session() as session:
            from sqlalchemy import select

            rec = (
                await session.execute(
                    select(Recommendation).where(
                        Recommendation.merchant_id == uuid.UUID(TEST_MERCHANT_ID)
                    )
                )
            ).scalars().first()
            rec_id = str(rec.id)

        res = await client.post(
            "/api/v1/purchase/from-advice",
            json={"items": [], "recommendation_ids": [rec_id]},
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"]["added_count"] == 1

    async def test_empty_items_with_unknown_recommendation_ids_still_404(
        self, client, db_session
    ):
        """items=[] 且指定的建议 ID 不存在 → 保持「资源不存在」404 语义。"""
        res = await client.post(
            "/api/v1/purchase/from-advice",
            json={"items": [], "recommendation_ids": [str(uuid.uuid4())]},
        )
        assert res.status_code == 404
        assert "未找到可用的采购建议" in res.json()["detail"]


class TestN11RankingAndMonthly:
    """N11（移交补丁）：/reports/product-ranking 的 limit/metric 校验与
    /reports/monthly、/reports/weekly 的未来日期校验。"""

    async def test_ranking_negative_limit_422(self, client):
        """limit=-5 → 422（此前被忽略返回 200）。"""
        res = await client.get("/api/v1/reports/product-ranking", params={"limit": -5})
        assert res.status_code == 422

    async def test_ranking_limit_too_large_422(self, client):
        res = await client.get("/api/v1/reports/product-ranking", params={"limit": 101})
        assert res.status_code == 422

    async def test_ranking_bad_metric_422(self, client):
        res = await client.get("/api/v1/reports/product-ranking", params={"metric": "banana"})
        assert res.status_code == 422

    async def test_ranking_valid_params_200(self, client):
        res = await client.get("/api/v1/reports/product-ranking")
        assert res.status_code == 200
        assert res.json()["code"] == 0

    async def test_monthly_future_date_422(self, client):
        """monthly 传未来月份/未来日期 → 422（此前返回 200 空数据）。"""
        res = await client.get(
            "/api/v1/reports/monthly", params={"end_date": "2099-01-01"}
        )
        assert res.status_code == 422
        assert "不能晚于今天" in res.json()["detail"]

    async def test_weekly_future_date_422(self, client):
        """weekly 与 monthly 同口径。"""
        res = await client.get(
            "/api/v1/reports/weekly", params={"end_date": "2099-01-01"}
        )
        assert res.status_code == 422

    async def test_specs_unknown_sku_404(self, client):
        """/skus/{id}/specs 对不存在 SKU → 404（与 aliases 同语义，此前 200 空数组）。"""
        res = await client.get(f"/api/v1/catalog/skus/{uuid.uuid4()}/specs")
        assert res.status_code == 404
        assert "SKU不存在" in res.json()["detail"]

    async def test_specs_existing_sku_200(self, client):
        """存在的 SKU → 200 空规格列表（无规格 ≠ 不存在）。"""
        sku_id = await _create_sku(client, "规格测试商品")
        res = await client.get(f"/api/v1/catalog/skus/{sku_id}/specs")
        assert res.status_code == 200
        assert res.json()["data"] == []
