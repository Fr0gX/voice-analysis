"""Authoritative M1 gold-window clustering with bounded NME implementations."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import eigsh
from sklearn.cluster import KMeans


@dataclass(frozen=True)
class GoldWindowSpec:
    window_id: str
    start_ms: int
    end_ms: int
    tier: str
    score: float
    weight: float
    loudness_db: float | None = None
    source_segment_indices: list[int] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class SpeakerAnchor:
    local_label: str
    vector: list[float]
    medoid_vector: list[float]
    source_window_ids: list[str]
    source_window_starts: list[int]
    speech_ms: int
    sample_count: int
    loudness_db: float | None
    intra_mean_dist: float
    nearest_other_dist: float | None
    margin: float | None
    purity_score: float
    core_radius: float
    boundary_radius: float
    accept_radius: float
    concentration: float
    quality_flags: list[str] = field(default_factory=lambda: ["gold_window_cluster"])
    representative_source: str = "gold_window_cluster_v1"

    def public_dict(self) -> dict[str, Any]:
        return {
            "label": self.local_label,
            "speech_ms": self.speech_ms,
            "sample_count": self.sample_count,
            "source_window_ids": list(self.source_window_ids),
            "average_loudness_db": self.loudness_db,
            "intra_mean_distance": self.intra_mean_dist,
            "nearest_other_distance": self.nearest_other_dist,
            "margin": self.margin,
            "purity_score": self.purity_score,
            "core_radius": self.core_radius,
            "boundary_radius": self.boundary_radius,
            "accept_radius": self.accept_radius,
            "concentration": self.concentration,
            "quality_flags": list(self.quality_flags),
            "representative_source": self.representative_source,
        }


def select_gold_windows(windows: list[dict[str, Any]]) -> tuple[list[GoldWindowSpec], dict[str, Any]]:
    weights = {"clean": 1.0, "usable": 0.70}
    selected: list[GoldWindowSpec] = []
    skipped: dict[str, int] = {}
    for index, row in enumerate(windows):
        tier = str(row.get("tier") or "").lower()
        if tier not in weights:
            skipped[tier or "missing_tier"] = skipped.get(tier or "missing_tier", 0) + 1
            continue
        if row.get("accepted") is False:
            skipped["not_accepted"] = skipped.get("not_accepted", 0) + 1
            continue
        start, end = int(row.get("start_ms") or 0), int(row.get("end_ms") or 0)
        if end <= start:
            skipped["invalid_range"] = skipped.get("invalid_range", 0) + 1
            continue
        selected.append(GoldWindowSpec(
            window_id=str(row.get("window_id") or row.get("source_window_id") or f"gold_window_{index:03d}"),
            start_ms=start,
            end_ms=end,
            tier=tier,
            score=float(row.get("score") or 0.0),
            weight=weights[tier],
            loudness_db=float(row["loudness_db"]) if isinstance(row.get("loudness_db"), (int, float)) else None,
            source_segment_indices=[int(value) for value in row.get("source_segment_indices") or []],
        ))
    return selected, {
        "stage": "gold_window_clip_slicing",
        "input_count": len(windows),
        "selected_count": len(selected),
        "skipped": skipped,
        "tier_weights": weights,
    }


def build_speakers(
    specs: list[GoldWindowSpec],
    embeddings: list[list[float] | None],
    *,
    dense_nme_max_bytes: int,
) -> tuple[list[SpeakerAnchor], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    for spec, raw in zip(specs, embeddings):
        vector = valid_vector(raw)
        if vector is None or spec.weight <= 0:
            invalid += 1
            continue
        rows.append({"spec": spec, "vector": vector, "weight": float(spec.weight)})
    if not rows:
        return [], {
            "pipeline_version": "gold_window_cluster_v1",
            "enabled": True,
            "input_count": len(specs),
            "usable_embedding_count": 0,
            "invalid_embedding_count": invalid,
            "reason": "no_usable_gold_window_embeddings",
            "speakers": [],
        }
    vectors = [row["vector"] for row in rows]
    weights = [row["weight"] for row in rows]
    k_nme, nme_audit = estimate_k_nme(vectors, weights, min_k=1, max_k=6, dense_nme_max_bytes=dense_nme_max_bytes)
    upper_k = min(6, len(rows))
    candidate_ks = sorted({max(1, min(upper_k, value)) for value in (k_nme - 1, k_nme, k_nme + 1)})
    candidates: list[dict[str, Any]] = []
    for k in candidate_ks:
        labels, method = weighted_kmeans(vectors, weights, k)
        metrics = _cluster_metrics(rows, labels)
        quality = (
            float(metrics["mean_concentration"]) * 0.85
            + float(metrics["min_nearest_other_dist"] or 0.0) * 0.75
            - float(metrics["mean_intra_dist"]) * 0.95
            - 0.04 * abs(k - k_nme)
        )
        candidates.append({"k": k, "labels": labels, "method": method, "quality": round(quality, 6), **metrics})
    selected = max(candidates, key=lambda item: (float(item["quality"]), -abs(int(item["k"]) - k_nme)))
    speakers = _speaker_anchors(rows, selected["labels"])
    speakers.sort(key=lambda speaker: min(speaker.source_window_starts))
    for index, speaker in enumerate(speakers):
        speaker.local_label = f"local_spk_{index}"
    return speakers, {
        "pipeline_version": "gold_window_cluster_v1",
        "enabled": True,
        "unit_source": "accepted_speech_windows.clean_usable",
        "input_count": len(specs),
        "usable_embedding_count": len(rows),
        "invalid_embedding_count": invalid,
        "tier_counts": _counts([row["spec"].tier for row in rows]),
        "tier_weights": {"clean": 1.0, "usable": 0.70, "weak": 0.0, "rejected": 0.0},
        "k_bounds": {"min_k": 1, "max_k": 6, "effective_max_k": upper_k},
        "nme": nme_audit,
        "k_nme": k_nme,
        "candidate_ks": candidate_ks,
        "selected_k": int(selected["k"]),
        "selected_quality": selected["quality"],
        "candidates": [{key: value for key, value in item.items() if key != "labels"} for item in candidates],
        "speakers": [speaker.public_dict() for speaker in speakers],
    }


def estimate_k_nme(
    vectors: list[list[float]],
    weights: list[float],
    *,
    min_k: int,
    max_k: int,
    dense_nme_max_bytes: int,
) -> tuple[int, dict[str, Any]]:
    n = len(vectors)
    if n <= 1:
        return 1, {"method": "singleton", "selected_k": 1, "candidates": []}
    max_k = max(1, min(max_k, n))
    min_k = max(1, min(min_k, max_k))
    arr = _normalize_rows(np.asarray(vectors, dtype=np.float64))
    weight_arr = np.asarray([max(0.001, float(value)) for value in weights], dtype=np.float64)
    median_cosine = _median_pairwise_cosine(arr)
    single_allowed = median_cosine is None or median_cosine >= 0.62
    # Account for similarity, weighted affinity, normalized Laplacian, ordering,
    # and temporary ufunc/eigensolver arrays that can overlap at peak.
    estimated_dense_bytes = n * n * 8 * 8
    if estimated_dense_bytes <= dense_nme_max_bytes:
        return _estimate_dense(arr, weight_arr, min_k, max_k, median_cosine, single_allowed)
    return _estimate_sparse(arr, weight_arr, min_k, max_k, median_cosine, single_allowed)


def _estimate_dense(arr, weights, min_k, max_k, median_cosine, single_allowed):
    sim = np.clip(arr @ arr.T, -1.0, 1.0)
    base = np.maximum(sim, 0.0) ** 2.0
    base *= np.sqrt(weights[:, None] * weights[None, :])
    np.fill_diagonal(base, 0.0)
    candidates: list[dict[str, Any]] = []
    best = None
    n = len(arr)
    for p in [value for value in (1, 2, 3, 5, 8) if value <= n - 1]:
        affinity = np.zeros_like(base)
        order = np.argsort(base, axis=1)[:, ::-1][:, :p]
        for i in range(n):
            affinity[i, order[i]] = base[i, order[i]]
        affinity = np.maximum(affinity, affinity.T)
        vals = _dense_laplacian_values(affinity)
        best = _consider_eigenvalues(vals, p, min_k, max_k, single_allowed, candidates, best)
    return _finish_nme(best, candidates, median_cosine, single_allowed, method="normalized_maximum_eigengap_dense")


def _estimate_sparse(arr, weights, min_k, max_k, median_cosine, single_allowed):
    n = len(arr)
    maximum_p = min(8, n - 1)
    neighbor_indices = np.empty((n, maximum_p), dtype=np.int32)
    neighbor_values = np.empty((n, maximum_p), dtype=np.float64)
    block = 256
    for start in range(0, n, block):
        stop = min(n, start + block)
        sim = np.clip(arr[start:stop] @ arr.T, -1.0, 1.0)
        sim = np.maximum(sim, 0.0) ** 2.0
        sim *= np.sqrt(weights[start:stop, None] * weights[None, :])
        for local, absolute in enumerate(range(start, stop)):
            sim[local, absolute] = 0.0
        part = np.argpartition(sim, -maximum_p, axis=1)[:, -maximum_p:]
        values = np.take_along_axis(sim, part, axis=1)
        order = np.argsort(values, axis=1)[:, ::-1]
        neighbor_indices[start:stop] = np.take_along_axis(part, order, axis=1)
        neighbor_values[start:stop] = np.take_along_axis(values, order, axis=1)
    candidates: list[dict[str, Any]] = []
    best = None
    for p in [value for value in (1, 2, 3, 5, 8) if value <= maximum_p]:
        rows = np.repeat(np.arange(n), p)
        cols = neighbor_indices[:, :p].reshape(-1)
        data = neighbor_values[:, :p].reshape(-1)
        affinity = sparse.csr_matrix((data, (rows, cols)), shape=(n, n)).maximum(
            sparse.csr_matrix((data, (cols, rows)), shape=(n, n))
        )
        degrees = np.asarray(affinity.sum(axis=1)).ravel()
        inv = np.zeros_like(degrees)
        inv[degrees > 1e-10] = 1.0 / np.sqrt(degrees[degrees > 1e-10])
        lap = sparse.eye(n, format="csr") - sparse.diags(inv) @ affinity @ sparse.diags(inv)
        count = min(n - 1, max_k + 1)
        try:
            vals = np.sort(np.real(eigsh(lap, k=count, which="SM", return_eigenvectors=False, v0=np.ones(n))))
        except Exception as exc:  # noqa: BLE001
            # A sparse-path failure must not silently materialize the N x N
            # matrix and defeat the memory decision that selected this path.
            raise RuntimeError("sparse NME eigensolver failed") from exc
        best = _consider_eigenvalues(vals, p, min_k, max_k, single_allowed, candidates, best)
    return _finish_nme(best, candidates, median_cosine, single_allowed, method="normalized_maximum_eigengap_sparse")


def _dense_laplacian_values(affinity: np.ndarray) -> np.ndarray:
    degrees = np.sum(affinity, axis=1)
    if float(np.max(degrees)) <= 1e-10:
        return np.asarray([])
    inv = np.zeros_like(degrees)
    inv[degrees > 1e-10] = 1.0 / np.sqrt(degrees[degrees > 1e-10])
    return np.sort(np.real(np.linalg.eigvalsh(np.eye(len(affinity)) - inv[:, None] * affinity * inv[None, :])))


def _consider_eigenvalues(vals, p, min_k, max_k, single_allowed, candidates, best):
    for k in range(min_k, max_k + 1):
        if k >= len(vals):
            continue
        gap = float(vals[k] - vals[k - 1])
        score = gap / max(1e-6, float(vals[k]) + 1e-6)
        rejected = k == 1 and not single_allowed
        candidates.append({
            "neighbors": p,
            "k": k,
            "nme_score": round(score, 6),
            "rejected": rejected,
            "reject_reason": "single_cluster_low_cohesion" if rejected else None,
        })
        if not rejected and (best is None or score > best[0]):
            best = (score, k, p, [float(value) for value in vals[: min(12, len(vals))]])
    return best


def _finish_nme(best, candidates, median_cosine, single_allowed, *, method):
    if best is None:
        return 1, {
            "method": "nme_failed_fallback",
            "selected_k": 1,
            "single_cluster_median_cosine": _round(median_cosine),
            "single_cluster_allowed": bool(single_allowed),
            "candidates": candidates[:16],
        }
    score, k, p, vals = best
    return int(k), {
        "method": method,
        "selected_k": int(k),
        "nme_score": round(float(score), 6),
        "neighbors": int(p),
        "single_cluster_median_cosine": _round(median_cosine),
        "single_cluster_allowed": bool(single_allowed),
        "eigenvalues": [round(float(value), 6) for value in vals],
        "candidates": sorted(candidates, key=lambda item: -float(item["nme_score"]))[:16],
    }


def weighted_kmeans(vectors: list[list[float]], weights: list[float], k: int) -> tuple[list[int], str]:
    if k <= 1 or len(vectors) <= 1:
        return [0] * len(vectors), "single_cluster"
    arr = _normalize_rows(np.asarray(vectors, dtype=np.float64))
    try:
        labels = KMeans(n_clusters=k, n_init=20, random_state=0).fit_predict(
            arr, sample_weight=np.asarray(weights, dtype=np.float64)
        )
        return _compact(labels.tolist()), "sklearn_kmeans_sample_weight"
    except Exception:  # noqa: BLE001
        return _spherical_kmeans(arr, weights, k), "numpy_weighted_spherical_kmeans"


def _spherical_kmeans(arr: np.ndarray, weights: list[float], k: int) -> list[int]:
    n = len(arr)
    centers = [0]
    while len(centers) < k:
        best_index, best_score = 0, -1.0
        for index in range(n):
            if index in centers:
                continue
            score = (1.0 - max(float(arr[index] @ arr[c]) for c in centers)) * max(0.001, weights[index])
            if score > best_score:
                best_index, best_score = index, score
        centers.append(best_index)
    centroids = arr[centers].copy()
    labels = np.zeros(n, dtype=int)
    weight_arr = np.asarray([max(0.001, value) for value in weights])
    for _ in range(80):
        similarities = arr @ centroids.T
        new_labels = np.argmax(similarities, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for ci in range(k):
            members = np.where(labels == ci)[0]
            if not members.size:
                centroids[ci] = arr[int(np.argmin(np.max(similarities, axis=1)))]
                continue
            center = np.sum(arr[members] * weight_arr[members, None], axis=0)
            norm = float(np.linalg.norm(center))
            centroids[ci] = center / norm if norm > 1e-12 else arr[int(members[0])]
    return _compact(labels.tolist())


def _cluster_metrics(rows: list[dict[str, Any]], labels: list[int]) -> dict[str, Any]:
    grouped: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        grouped.setdefault(int(label), []).append(index)
    centers: dict[int, list[float]] = {}
    clusters: list[dict[str, Any]] = []
    weighted_intra = total_weight = 0.0
    concentrations: list[float] = []
    for label, members in sorted(grouped.items()):
        vectors = [rows[i]["vector"] for i in members]
        weights = [rows[i]["weight"] for i in members]
        center, concentration = _weighted_spherical_mean(vectors, weights)
        centers[label] = center
        distances = [1.0 - cosine(rows[i]["vector"], center) for i in members]
        intra = _weighted_mean(distances, weights)
        weight_sum = sum(weights)
        weighted_intra += intra * weight_sum
        total_weight += weight_sum
        concentrations.append(concentration)
        clusters.append({
            "label": label,
            "member_count": len(members),
            "weight": round(weight_sum, 4),
            "speech_ms": sum(rows[i]["spec"].duration_ms for i in members),
            "intra_mean_dist": round(intra, 6),
            "core_radius": round(_weighted_quantile(distances, weights, 0.70), 6),
            "boundary_radius": round(_weighted_quantile(distances, weights, 0.90), 6),
            "concentration": round(concentration, 6),
            "source_window_ids": [rows[i]["spec"].window_id for i in members],
        })
    nearest_values: list[float] = []
    for cluster in clusters:
        label = cluster["label"]
        others = [1.0 - cosine(centers[label], value) for other, value in centers.items() if other != label]
        nearest = min(others) if others else None
        cluster["nearest_other_dist"] = None if nearest is None else round(nearest, 6)
        if nearest is not None:
            nearest_values.append(nearest)
    return {
        "cluster_count": len(clusters),
        "mean_intra_dist": round(weighted_intra / max(1e-9, total_weight), 6),
        "mean_concentration": round(sum(concentrations) / max(1, len(concentrations)), 6),
        "min_nearest_other_dist": round(min(nearest_values), 6) if nearest_values else None,
        "clusters": clusters,
    }


def _speaker_anchors(rows: list[dict[str, Any]], labels: list[int]) -> list[SpeakerAnchor]:
    grouped: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        grouped.setdefault(int(label), []).append(index)
    speakers: list[SpeakerAnchor] = []
    for label, members in sorted(grouped.items(), key=lambda item: min(item[1])):
        vectors = [rows[i]["vector"] for i in members]
        weights = [rows[i]["weight"] for i in members]
        center, concentration = _weighted_spherical_mean(vectors, weights)
        distances = [1.0 - cosine(rows[i]["vector"], center) for i in members]
        core = _weighted_quantile(distances, weights, 0.70)
        boundary = _weighted_quantile(distances, weights, 0.90)
        medoid_index = max(members, key=lambda i: cosine(rows[i]["vector"], center))
        loudness = [rows[i]["spec"].loudness_db for i in members if rows[i]["spec"].loudness_db is not None]
        purity = max(0.0, min(1.0, concentration * (1.0 - boundary)))
        speakers.append(SpeakerAnchor(
            local_label=f"local_spk_{label}",
            vector=center,
            medoid_vector=list(rows[medoid_index]["vector"]),
            source_window_ids=[rows[i]["spec"].window_id for i in members],
            source_window_starts=[rows[i]["spec"].start_ms for i in members],
            speech_ms=sum(rows[i]["spec"].duration_ms for i in members),
            sample_count=len(members),
            loudness_db=round(sum(loudness) / len(loudness), 2) if loudness else None,
            intra_mean_dist=round(_weighted_mean(distances, weights), 4),
            nearest_other_dist=None,
            margin=None,
            purity_score=round(purity, 4),
            core_radius=round(core, 4),
            boundary_radius=round(boundary, 4),
            accept_radius=round(boundary, 4),
            concentration=round(concentration, 4),
        ))
    for speaker in speakers:
        others = [1.0 - cosine(speaker.vector, other.vector) for other in speakers if other is not speaker]
        nearest = min(others) if others else None
        speaker.nearest_other_dist = None if nearest is None else round(nearest, 4)
        speaker.margin = None if nearest is None else round(nearest - speaker.intra_mean_dist, 4)
    return speakers


def valid_vector(value: Any) -> list[float] | None:
    if not isinstance(value, (list, np.ndarray)) or len(value) == 0:
        return None
    try:
        vector = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    norm = math.sqrt(sum(item * item for item in vector))
    if norm <= 1e-12:
        return None
    return [item / norm for item in vector]


def cosine(left: list[float], right: list[float]) -> float:
    lvec, rvec = valid_vector(left), valid_vector(right)
    if lvec is None or rvec is None:
        return 0.0
    return sum(lvec[index] * rvec[index] for index in range(min(len(lvec), len(rvec))))


def _normalize_rows(arr: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms <= 1e-12] = 1.0
    return arr / norms


def _weighted_spherical_mean(vectors, weights):
    arr = _normalize_rows(np.asarray(vectors, dtype=np.float64))
    weight_arr = np.asarray([max(0.001, float(value)) for value in weights])
    center = np.sum(arr * weight_arr[:, None], axis=0)
    norm = float(np.linalg.norm(center))
    center = center / norm if norm > 1e-12 else arr[0]
    concentration = float(np.sum((arr @ center) * weight_arr) / max(1e-9, np.sum(weight_arr)))
    return [float(value) for value in center], concentration


def _weighted_mean(values, weights):
    return sum(float(v) * float(w) for v, w in zip(values, weights)) / max(1e-9, sum(float(w) for w in weights))


def _weighted_quantile(values, weights, q):
    pairs = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(max(0.0, float(weight)) for _, weight in pairs)
    if total <= 0:
        return float(pairs[-1][0]) if pairs else 0.0
    threshold = total * q
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += max(0.0, float(weight))
        if cumulative >= threshold:
            return float(value)
    return float(pairs[-1][0])


def _median_pairwise_cosine(arr: np.ndarray) -> float | None:
    if len(arr) < 2:
        return None
    values = [float(arr[i] @ arr[j]) for i in range(len(arr)) for j in range(i + 1, len(arr))]
    return float(np.median(values)) if values else None


def _compact(labels):
    mapping: dict[int, int] = {}
    out: list[int] = []
    for raw in labels:
        raw = int(raw)
        if raw not in mapping:
            mapping[raw] = len(mapping)
        out.append(mapping[raw])
    return out


def _counts(values):
    result: dict[str, int] = {}
    for value in values:
        result[str(value)] = result.get(str(value), 0) + 1
    return result


def _round(value):
    return None if value is None else round(float(value), 6)
