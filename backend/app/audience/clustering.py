import numpy as np
import hdbscan
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass


@dataclass
class SegmentInfo:
    label: str
    centroid: List[float]
    size: int
    profile_indices: List[int]


def cluster_audience_embeddings(
    embeddings: List[List[float]],
    min_cluster_size: int = 5,
    min_samples: int = 2,
) -> List[SegmentInfo]:
    """
    Applies HDBSCAN over audience interest embeddings.
    HDBSCAN does not require a predefined number of clusters, letting
    the natural density of audience interactions determine segments.
    """
    if not embeddings:
        return []

    X = np.array(embeddings, dtype=np.float32)
    n_samples, dim = X.shape

    if n_samples < min_cluster_size:
        # Not enough samples for HDBSCAN clustering: single overall segment
        centroid = np.mean(X, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid /= norm
        return [
            SegmentInfo(
                label="Core Audience (Unified)",
                centroid=centroid.tolist(),
                size=n_samples,
                profile_indices=list(range(n_samples))
            )
        ]

    # Run HDBSCAN
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min(min_cluster_size, n_samples // 2),
        min_samples=min_samples,
        metric="euclidean",
        cluster_selection_epsilon=0.15,
    )
    labels = clusterer.fit_predict(X)

    unique_labels = set(labels)
    segments: List[SegmentInfo] = []

    # Map cluster id to topic/category names or labels
    segment_names = [
        "Tech Enthusiasts",
        "Casual Browsers",
        "Deep Practitioners",
        "Industry Observers",
        "Creative Explorers",
        "Active Discussants",
    ]

    for label in unique_labels:
        mask = labels == label
        indices = np.where(mask)[0].tolist()
        size = len(indices)
        if size == 0:
            continue

        cluster_points = X[mask]
        centroid = np.mean(cluster_points, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid /= norm

        if label == -1:
            segment_label = "Long-tail / Noise Segment"
        else:
            name_idx = label % len(segment_names)
            segment_label = f"Segment {label + 1}: {segment_names[name_idx]}"

        segments.append(
            SegmentInfo(
                label=segment_label,
                centroid=centroid.tolist(),
                size=size,
                profile_indices=indices,
            )
        )

    # Sort by cluster size descending
    segments.sort(key=lambda s: s.size, reverse=True)
    return segments
