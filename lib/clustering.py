"""Generic proximity clustering of Bbox rects: no layout-domain knowledge,
just gap-expanded intersection merging. Shared by figure/drawing-cluster
detection and dense small-text-block clustering in workflow/figures.py."""

from lib.elements import Bbox


def cluster_bboxes(bboxes: list[Bbox], gap: float) -> list[Bbox]:
    """Greedy proximity clustering: merge any bboxes whose gap-expanded
    extents intersect, repeated to a fixed point. Shared by drawing-fragment
    clustering and dense small-text-block clustering (e.g. borderless
    tables built from many individual math-symbol spans)."""
    changed = True
    pending = list(bboxes)
    while changed:
        changed = False
        new_clusters: list[Bbox] = []
        used = [False] * len(pending)
        for i, bb in enumerate(pending):
            if used[i]:
                continue
            cur = bb
            used[i] = True
            for j in range(i + 1, len(pending)):
                if used[j]:
                    continue
                other = pending[j]
                expanded = Bbox(cur.x0 - gap, cur.y0 - gap, cur.x1 + gap, cur.y1 + gap)
                if expanded.intersects(other):
                    cur = cur.union(other)
                    used[j] = True
                    changed = True
            new_clusters.append(cur)
        pending = new_clusters
    return pending


def cluster_indices(bboxes: list[Bbox], gap: float) -> list[list[int]]:
    """Like cluster_bboxes but returns groups of original indices, so
    callers can trace merged regions back to their source blocks."""
    groups = [[i] for i in range(len(bboxes))]
    cur_bboxes = list(bboxes)
    changed = True
    while changed:
        changed = False
        new_groups: list[list[int]] = []
        new_bboxes: list[Bbox] = []
        used = [False] * len(cur_bboxes)
        for i, bb in enumerate(cur_bboxes):
            if used[i]:
                continue
            cur = bb
            members = list(groups[i])
            used[i] = True
            for j in range(i + 1, len(cur_bboxes)):
                if used[j]:
                    continue
                other = cur_bboxes[j]
                expanded = Bbox(cur.x0 - gap, cur.y0 - gap, cur.x1 + gap, cur.y1 + gap)
                if expanded.intersects(other):
                    cur = cur.union(other)
                    members.extend(groups[j])
                    used[j] = True
                    changed = True
            new_groups.append(members)
            new_bboxes.append(cur)
        groups, cur_bboxes = new_groups, new_bboxes
    return groups
