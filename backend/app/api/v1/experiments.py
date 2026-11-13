import asyncio
import numpy as np
from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db, AsyncSessionLocal
from app.models.entities import Experiment, SimulationRun, MetricsSnapshot
from app.schemas.api import ExperimentCreateRequest, ExperimentResponse
from app.simulation.simulator import MultiPolicySimulator

router = APIRouter(tags=["Experiments & Simulation"])


@router.post("/experiments", response_model=ExperimentResponse)
async def create_and_run_experiment(req: ExperimentCreateRequest, db: AsyncSession = Depends(get_db)):
    experiment = Experiment(
        name=req.name,
        policy_type="comparative_bandit_suite",
        config={"n_rounds": req.n_rounds, "seed": req.seed, "alpha": req.alpha},
        started_at=datetime.now(timezone.utc),
    )
    db.add(experiment)
    await db.commit()
    await db.refresh(experiment)

    # Run simulation
    simulator = MultiPolicySimulator(
        n_rounds=req.n_rounds,
        seed=req.seed,
        alpha=req.alpha,
    )
    sim_results = simulator.run_simulation()

    # Save SimulationRun
    run = SimulationRun(
        experiment_id=experiment.id,
        seed=req.seed,
        audience_profile_config={"num_users": 300, "dim": 24},
        n_rounds=req.n_rounds,
        # Криві зберігаються разом зі зведенням: без цього перегляд результатів
        # змушений був повторювати весь прогін симуляції на кожен запит.
        results={"summary": sim_results["summary"], "history": sim_results["history"]},
    )
    db.add(run)

    # Save MetricsSnapshots
    history = sim_results["history"]
    bandit_hist = history["bandit"]
    for i in range(len(bandit_hist["round"])):
        snap = MetricsSnapshot(
            experiment_id=experiment.id,
            round_number=bandit_hist["round"][i],
            cumulative_reward=bandit_hist["cumulative_reward"][i],
            regret=bandit_hist["cumulative_regret"][i],
            ctr=bandit_hist["ctr"][i],
            computed_at=datetime.now(timezone.utc),
        )
        db.add(snap)

    experiment.finished_at = datetime.now(timezone.utc)
    await db.commit()

    return ExperimentResponse(
        id=experiment.id,
        name=experiment.name,
        policy_type=experiment.policy_type,
        started_at=experiment.started_at,
        finished_at=experiment.finished_at,
        summary=sim_results["summary"],
    )


@router.get("/experiments", response_model=List[ExperimentResponse])
async def list_experiments(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Experiment).order_by(Experiment.started_at.desc()))
    exps = res.scalars().all()
    results = []
    for exp in exps:
        # Fetch run summary if present
        run_res = await db.execute(
            select(SimulationRun).where(SimulationRun.experiment_id == exp.id)
        )
        run = run_res.scalar_one_or_none()
        results.append(
            ExperimentResponse(
                id=exp.id,
                name=exp.name,
                policy_type=exp.policy_type,
                started_at=exp.started_at,
                finished_at=exp.finished_at,
                summary=_stored_summary(run),
            )
        )
    return results


def _stored_summary(run) -> dict:
    """Сумісність зі старими рядками, де в results лежало саме зведення."""
    data = (run.results if run else None) or {}
    return data.get("summary", data) if isinstance(data, dict) else {}


@router.get("/experiments/{exp_id}/results")
async def get_experiment_results(exp_id: int, db: AsyncSession = Depends(get_db)):
    exp_res = await db.execute(select(Experiment).where(Experiment.id == exp_id))
    exp = exp_res.scalar_one_or_none()
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")

    run_res = await db.execute(select(SimulationRun).where(SimulationRun.experiment_id == exp_id))
    run = run_res.scalar_one_or_none()

    stored = (run.results if run else None) or {}
    summary = _stored_summary(run)
    history = stored.get("history") if isinstance(stored, dict) else None

    # Для експериментів, збережених до появи персистентних кривих, прогін
    # відтворюється за тим самим зерном — результат ідентичний, лише повільніше.
    if not history:
        cfg = exp.config or {}
        data = MultiPolicySimulator(
            n_rounds=cfg.get("n_rounds", 100),
            seed=cfg.get("seed", 42),
            alpha=cfg.get("alpha", 0.8),
        ).run_simulation()
        history = data["history"]
        summary = summary or data["summary"]

    return {
        "experiment_id": exp.id,
        "name": exp.name,
        "config": exp.config,
        "summary": summary,
        "history": history,
    }


@router.websocket("/ws/experiments/{exp_id}/live")
async def experiment_live_websocket(websocket: WebSocket, exp_id: int):
    await websocket.accept()
    try:
        # Stream live simulation progress round-by-round
        sim = MultiPolicySimulator(n_rounds=60, seed=42)
        # Run step-by-step
        from app.simulation.audience_model import SyntheticAudienceSegment, SegmentSimulationConfig
        from app.decision.baselines import (
            ContextualBanditPolicy, RandomPolicy, FixedHeuristicPolicy, GreedyHistoricalAveragePolicy
        )
        from app.decision.feedback import compute_reward

        policies = {
            "bandit": ContextualBanditPolicy(base_context_dim=sim.dim, alpha=sim.alpha),
            "random": RandomPolicy(seed=sim.seed),
            "fixed_heuristic": FixedHeuristicPolicy(),
            "greedy_avg": GreedyHistoricalAveragePolicy(),
        }
        envs = {k: SyntheticAudienceSegment(SegmentSimulationConfig(num_users=250, dim=sim.dim, seed=sim.seed)) for k in policies}
        oracle_env = SyntheticAudienceSegment(SegmentSimulationConfig(num_users=250, dim=sim.dim, seed=sim.seed))

        cum_r = {k: 0.0 for k in policies}
        cum_regret = {k: 0.0 for k in policies}
        ctx_rng = np.random.RandomState(sim.seed + 10)

        for round_num in range(1, 61):
            ctx = ctx_rng.randn(sim.dim)
            ctx /= np.linalg.norm(ctx)
            ctx_list = ctx.tolist()

            # Oracle step
            best_r = 0.0
            for _ in range(15):
                cand = {d: ctx_rng.choice(arms) for d, arms in sim.arm_vectors.items()}
                vec = sim._arms_to_vector(cand)
                st = oracle_env.evaluate_content_step(vec)
                rw = compute_reward(st["views"], st["reactions"], st["comments"], st["saves"], st["shares"])
                if rw > best_r:
                    best_r = rw

            step_data = {"round": round_num, "policies": {}}
            for name, pol in policies.items():
                chosen = pol.select(ctx_list)
                vec = sim._arms_to_vector(chosen)
                stats = envs[name].evaluate_content_step(vec)
                reward = compute_reward(stats["views"], stats["reactions"], stats["comments"], stats["saves"], stats["shares"])
                pol.update(ctx_list, chosen, reward)

                instant_regret = max(0.0, best_r - reward)
                cum_r[name] += reward
                cum_regret[name] += instant_regret

                step_data["policies"][name] = {
                    "reward": round(reward, 4),
                    "cumulative_reward": round(cum_r[name], 4),
                    "regret": round(instant_regret, 4),
                    "cumulative_regret": round(cum_regret[name], 4),
                    "ctr": stats["ctr"],
                    "chosen_arms": chosen,
                }

            await websocket.send_json(step_data)
            await asyncio.sleep(0.12)  # smooth animation streaming

        await websocket.send_json({"type": "COMPLETED"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
