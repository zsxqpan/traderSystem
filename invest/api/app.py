"""FastAPI 应用：读接口复用仪表盘查询层，写接口复用执行纪律模块。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from invest.config import get_settings
from invest.db import connect


class PoolBody(BaseModel):
    symbol: str
    level: str = "track"
    industry: str = ""
    reason: str = ""


class RatingBody(BaseModel):
    kind: str
    value: str
    basis: str = ""


class PlanBody(BaseModel):
    symbol: str
    stop_loss: float | None = None
    target_position: float | None = None
    buy_range: str = ""
    take_profit: str = ""


class TradeBody(BaseModel):
    plan_id: int
    action: str
    price: float
    qty: int
    emotion: str = ""


def create_app(db_path: str | None = None) -> FastAPI:
    from dashboard import queries as q

    settings = get_settings()
    DB = db_path or settings.db_path

    app = FastAPI(title="A股投资系统 API", version="0.1.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "db": DB}

    @app.get("/api/strength")
    def strength(period: str = "short", top: int = 20):
        df = q.load_strength(DB) if period == "short" else q.load_weekly(DB)
        return df.head(top).to_dict(orient="records")

    @app.get("/api/temperature")
    def temperature():
        return q.load_temperature(DB).to_dict(orient="records")

    @app.get("/api/rotation")
    def rotation(top: int = 20):
        return q.load_rotation(DB).head(top).to_dict(orient="records")

    @app.get("/api/capital")
    def capital():
        return q.load_capital(DB).to_dict(orient="records")

    @app.get("/api/linkage")
    def linkage(threshold: float = 0.8, top: int = 20):
        df = q.load_linkage(DB)
        df = df[df["corr"] >= threshold] if "corr" in df.columns else df
        return df.head(top).to_dict(orient="records")

    @app.get("/api/crowding")
    def crowding():
        return q.load_crowding(DB).to_dict(orient="records")

    @app.get("/api/macro")
    def macro():
        return q.load_macro(DB).to_dict(orient="records")

    @app.get("/api/viewpoints")
    def viewpoints(status: str | None = None, limit: int = 100):
        return q.load_viewpoints(DB, status=status, limit=limit).to_dict(orient="records")

    @app.get("/api/accuracy")
    def accuracy():
        return q.load_accuracy(DB).to_dict(orient="records")

    @app.get("/api/pool")
    def pool_list():
        return q.load_pool(DB).to_dict(orient="records")

    @app.get("/api/ratings")
    def ratings():
        return q.load_ratings(DB).to_dict(orient="records")

    @app.get("/api/plans")
    def plans():
        return q.load_plans(DB).to_dict(orient="records")

    @app.get("/api/records")
    def records(limit: int = 100):
        df = q.load_records(DB)
        return df.head(limit).to_dict(orient="records")

    @app.get("/api/backtests")
    def backtests():
        return q.load_backtests(DB).to_dict(orient="records")

    @app.get("/api/jobs")
    def jobs(limit: int = 20):
        return q.load_jobs(DB, limit=limit).to_dict(orient="records")

    @app.get("/api/coverage")
    def coverage():
        return q.load_coverage(DB).to_dict(orient="records")

    # ---------- 写接口 ----------
    @app.post("/api/pool")
    def pool_add(body: PoolBody):
        from invest.discipline import pool as pool_mod
        conn = connect(DB)
        try:
            return pool_mod.add_to_pool(
                conn, body.symbol, level=body.level, industry=body.industry, reason=body.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            conn.close()

    @app.post("/api/rating")
    def rating_set(body: RatingBody):
        from invest.discipline import rating as rating_mod
        conn = connect(DB)
        try:
            rating_mod.set_rating(conn, body.kind, body.value, basis_json=body.basis)
            return {"ok": True}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            conn.close()

    @app.post("/api/plan")
    def plan_create(body: PlanBody):
        from invest.discipline import plans as plans_mod
        conn = connect(DB)
        try:
            return plans_mod.create_plan(
                conn, body.symbol, buy_range=body.buy_range,
                target_position=body.target_position, stop_loss=body.stop_loss,
                take_profit=body.take_profit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            conn.close()

    @app.post("/api/trade")
    def trade_add(body: TradeBody):
        from invest.discipline import records as records_mod
        conn = connect(DB)
        try:
            return records_mod.record_trade(
                conn, body.plan_id, body.action, body.price, body.qty, emotion_note=body.emotion,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            conn.close()

    return app


app = create_app()