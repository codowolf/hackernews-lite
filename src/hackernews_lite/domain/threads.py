"""Pure: the whole thread a comment belongs to, flattened for display with the comment marked.

A top-level comment nobody replied to would show as a single comment, so it is shown among the
comments around it instead: its neighbours in Hacker News's ranking, whole threads at a time.
"""

from dataclasses import dataclass
from typing import Literal

from hackernews_lite.domain.models import Comment

Relation = Literal["target", "ancestor", "reply", "other"]

AROUND_BUDGET = 40  # most comments to add around a comment that has no replies


@dataclass(frozen=True)
class ThreadNode:
    comment: Comment
    depth: int  # 0 for the top-level comment, 1 for its replies, and so on
    relation: Relation  # how it relates to the comment the reader asked about


@dataclass(frozen=True)
class ThreadSlice:
    rank: int  # where the thread's top-level comment sits in Hacker News's ranking
    total: int  # how many top-level comments the story has
    nodes: list[ThreadNode]  # depth first, in Hacker News's order
    around: bool = False  # the comment has no replies, so its neighbours were added for context


def thread_slice(comments: list[Comment], comment_id: int) -> ThreadSlice | None:
    """The thread containing `comment_id`, or None if that comment is not in the saved tree."""
    for index, top in enumerate(comments):
        path = _path_to(top, comment_id)
        if path is None:
            continue
        nodes = _flatten(top, path, comment_id)
        around = len(nodes) == 1  # a lone top-level comment: nothing else to read
        if around:
            nodes = _with_neighbours(comments, index, nodes)
        return ThreadSlice(rank=index + 1, total=len(comments), nodes=nodes, around=around)
    return None


def _with_neighbours(
    comments: list[Comment], index: int, own: list[ThreadNode]
) -> list[ThreadNode]:
    """Whole neighbouring threads, nearest first and alternating sides, within a size budget."""
    before: list[list[ThreadNode]] = []  # nearest first
    after: list[list[ThreadNode]] = []
    used = len(own)
    open_sides = {"after": index + 1, "before": index - 1}
    while open_sides:
        for side in list(open_sides):
            at = open_sides[side]
            if not 0 <= at < len(comments):
                del open_sides[side]
                continue
            tree = _flatten(comments[at], [], -1)
            if used + len(tree) > AROUND_BUDGET:
                del open_sides[side]
                continue
            used += len(tree)
            (after if side == "after" else before).append(tree)
            open_sides[side] = at + 1 if side == "after" else at - 1
    return [n for tree in reversed(before) for n in tree] + own + [n for t in after for n in t]


def _path_to(node: Comment, comment_id: int) -> list[int] | None:
    """Ids from `node` down to the comment, both ends included, or None if it is not below."""
    if node.id == comment_id:
        return [node.id]
    for child in node.children:
        below = _path_to(child, comment_id)
        if below is not None:
            return [node.id, *below]
    return None


def _flatten(top: Comment, path: list[int], target_id: int) -> list[ThreadNode]:
    ancestors = set(path[:-1])
    nodes: list[ThreadNode] = []

    def walk(node: Comment, depth: int, under_target: bool) -> None:
        if node.id == target_id:
            relation: Relation = "target"
        elif node.id in ancestors:
            relation = "ancestor"
        elif under_target:
            relation = "reply"
        else:
            relation = "other"
        nodes.append(ThreadNode(comment=node, depth=depth, relation=relation))
        for child in node.children:
            walk(child, depth + 1, under_target or node.id == target_id)

    walk(top, 0, False)
    return nodes
