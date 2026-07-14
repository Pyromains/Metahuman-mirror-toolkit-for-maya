# -*- coding: utf-8 -*-
"""
MetaHuman Mirror Toolkit v1.1.0
Autodesk Maya / Python 3

A focused toolkit for Epic MetaHuman head_lod0 meshes sharing the same
official topology and vertex order.

Stable features
---------------
- Load a validated left/right mirror map.
- Snap selected target vertices to their anatomical opposite on a mirrored
  reference mesh.
- Axis filters: X, Y, Z.
- Blend interpolation.
- Mirror-distance analysis on a selection or the full target mesh.
- Threshold-based vertex selection.
- Same-topology direct vertex transfer (non-mirrored).
- Same-topology mesh comparison.
- Largest-difference list with direct Maya selection.
- Session progress history and CSV export.
- Append-only OBJ mesh revision snapshots with comments and restore tools.

Removed from this public-ready build
------------------------------------
- Soft Selection / falloff experiments.
- Heatmap experiments.
- Different-topology surface comparison.

Expected topology
-----------------
The supplied mirror map targets MetaHuman head_lod0_mesh with 24,049 vertices.
"""

from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

import mesh_revision_manager


VERSION = "1.1.0"
WINDOW_NAME = "metaHumanMirrorToolkitWindow"
WINDOW_TITLE = f"MetaHuman Mirror Toolkit v{VERSION}"

MIRROR_MAP: Optional[Dict] = None
UI: Dict[str, str] = {}
SESSION_HISTORY: List[Dict] = []
COMPARE_ROWS: List[Tuple[int, float]] = []


# -----------------------------------------------------------------------------
# Mesh helpers
# -----------------------------------------------------------------------------


def _as_transform(node: str) -> str:
    if not node or not cmds.objExists(node):
        raise RuntimeError("The specified node does not exist.")

    node = cmds.ls(node, long=True)[0]

    if cmds.nodeType(node) == "mesh":
        parents = cmds.listRelatives(
            node,
            parent=True,
            fullPath=True,
        ) or []
        if not parents:
            raise RuntimeError("The mesh transform could not be found.")
        return parents[0]

    shapes = cmds.listRelatives(
        node,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
    ) or []

    if not any(cmds.nodeType(shape) == "mesh" for shape in shapes):
        raise RuntimeError("The selected node does not contain a mesh.")

    return node


def _mesh_shape(node: str) -> str:
    transform = _as_transform(node)
    shapes = cmds.listRelatives(
        transform,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
    ) or []

    for shape in shapes:
        if cmds.nodeType(shape) == "mesh":
            return shape

    raise RuntimeError("No valid mesh shape was found.")


def _mesh_fn(node: str) -> om.MFnMesh:
    selection = om.MSelectionList()
    selection.add(_mesh_shape(node))
    return om.MFnMesh(selection.getDagPath(0))


def _world_points(node: str) -> List[om.MPoint]:
    return list(_mesh_fn(node).getPoints(om.MSpace.kWorld))


def _selected_mesh() -> str:
    selection = cmds.ls(
        selection=True,
        objectsOnly=True,
        long=True,
    ) or []

    if not selection:
        raw = cmds.ls(
            selection=True,
            flatten=True,
            long=True,
        ) or []
        if raw:
            selection = [raw[-1].split(".")[0]]

    if not selection:
        raise RuntimeError("Select a mesh in Object Mode.")

    return _as_transform(selection[-1])


def _selected_vertices(
    required: bool = True,
) -> Tuple[Optional[str], List[int], List[str]]:
    raw = cmds.ls(
        selection=True,
        flatten=True,
        long=True,
    ) or []

    components = [
        item for item in raw
        if ".vtx[" in item
    ]

    if not components:
        if required:
            raise RuntimeError(
                "Select vertices on the Target Mesh."
            )
        return None, [], []

    meshes = {
        component.split(".vtx[")[0]
        for component in components
    }

    if len(meshes) != 1:
        raise RuntimeError(
            "All selected vertices must belong to the same mesh."
        )

    mesh = next(iter(meshes))
    indices = [
        int(component.rsplit("[", 1)[1][:-1])
        for component in components
    ]

    return mesh, indices, components


def _field_mesh(key: str, label: str) -> str:
    node = cmds.textFieldButtonGrp(
        UI[key],
        query=True,
        text=True,
    )
    if not node:
        raise RuntimeError(f"Set the {label} first.")
    return _as_transform(node)


def _reference_mesh() -> str:
    return _field_mesh(
        "reference",
        "Reference Mesh (Mirrored)",
    )


def _target_mesh() -> str:
    return _field_mesh(
        "target",
        "Target Mesh",
    )


def _compare_mesh() -> str:
    return _field_mesh(
        "compare",
        "Compare Mesh",
    )


def _validate_topology(*meshes: str) -> int:
    if MIRROR_MAP is None:
        raise RuntimeError("Load the Mirror Map JSON first.")

    expected = int(MIRROR_MAP["vertex_count"])

    for mesh in meshes:
        count = _mesh_fn(mesh).numVertices
        if count != expected:
            raise RuntimeError(
                f"{mesh} has {count} vertices; "
                f"the mirror map expects {expected}."
            )

    return expected



def _validate_same_topology(*meshes: str) -> int:
    """
    Validate meshes that must share the same vertex count.

    This check deliberately does not require a Mirror Map. It is used by
    direct mesh comparison and non-mirrored vertex transfer. Same vertex
    order is assumed by the workflow, not verified here.
    """
    if not meshes:
        raise RuntimeError("No meshes were provided.")

    counts = [
        (_as_transform(mesh), _mesh_fn(mesh).numVertices)
        for mesh in meshes
    ]

    expected = counts[0][1]

    for mesh, count in counts[1:]:
        if count != expected:
            details = ", ".join(
                f"{name}: {value}"
                for name, value in counts
            )
            raise RuntimeError(
                "The meshes do not have the same vertex count. "
                + details
            )

    return expected


# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------


def _set_status(message: str, success: bool = False) -> None:
    print(f"[MetaHuman Mirror Toolkit] {message}")

    control = UI.get("status")
    if control and cmds.control(control, exists=True):
        cmds.text(
            control,
            edit=True,
            label=message,
            backgroundColor=(
                (0.20, 0.38, 0.20)
                if success
                else (0.38, 0.22, 0.18)
            ),
        )


def _guard(callback):
    def wrapped(*args):
        try:
            return callback(*args)
        except Exception as exc:
            cmds.warning(str(exc))
            _set_status(str(exc), False)
            return None

    return wrapped


def _set_mesh_from_selection(field_key: str) -> None:
    mesh = _selected_mesh()
    cmds.textFieldButtonGrp(
        UI[field_key],
        edit=True,
        text=mesh,
    )
    _set_status(f"Mesh set: {mesh}", True)


def _mode_value(control_key: str) -> str:
    return cmds.optionMenuGrp(
        UI[control_key],
        query=True,
        value=True,
    )


# -----------------------------------------------------------------------------
# Mirror Map
# -----------------------------------------------------------------------------


def load_mirror_map(path: str) -> None:
    global MIRROR_MAP

    with open(path, "r", encoding="utf-8") as stream:
        payload = json.load(stream)

    mapping = {
        int(key): int(value)
        for key, value in payload["mapping"].items()
    }
    vertex_count = int(payload["vertex_count"])

    if len(mapping) != vertex_count:
        raise RuntimeError(
            "The mirror map does not contain every vertex."
        )

    for index in range(vertex_count):
        if index not in mapping:
            raise RuntimeError(
                f"Missing mirror-map index: {index}."
            )

        opposite = mapping[index]

        if opposite < 0 or opposite >= vertex_count:
            raise RuntimeError(
                f"Invalid opposite index: {index} -> {opposite}."
            )

        if mapping.get(opposite) != index:
            raise RuntimeError(
                f"The mirror map is not bijective at "
                f"{index} -> {opposite}."
            )

    MIRROR_MAP = {
        "vertex_count": vertex_count,
        "mapping": mapping,
        "path": path,
    }

    _set_status(
        f"Mirror map loaded: {vertex_count} vertices.",
        True,
    )


@_guard
def browse_mirror_map(*_args) -> None:
    result = cmds.fileDialog2(
        fileMode=1,
        caption="Load Mirror Map",
        fileFilter="JSON (*.json)",
    )

    if not result:
        return

    path = result[0]
    load_mirror_map(path)

    cmds.textFieldButtonGrp(
        UI["map"],
        edit=True,
        text=path,
    )


# -----------------------------------------------------------------------------
# Distances and statistics
# -----------------------------------------------------------------------------


def _axis_distance(
    point_a: om.MPoint,
    point_b: om.MPoint,
    mode: str,
) -> float:
    dx = abs(point_b.x - point_a.x)
    dy = abs(point_b.y - point_a.y)
    dz = abs(point_b.z - point_a.z)

    if mode == "X":
        return dx
    if mode == "Y":
        return dy
    if mode == "Z":
        return dz

    return math.sqrt(
        (dx * dx)
        + (dy * dy)
        + (dz * dz)
    )


def _statistics(values: Iterable[float]) -> Dict[str, float]:
    ordered = sorted(float(value) for value in values)

    if not ordered:
        raise RuntimeError("No distances are available to analyse.")

    count = len(ordered)
    middle = count // 2

    median = (
        ordered[middle]
        if count % 2
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )

    return {
        "count": count,
        "min": ordered[0],
        "average": sum(ordered) / count,
        "median": median,
        "max": ordered[-1],
    }


def _format_stats(stats: Dict[str, float]) -> str:
    return (
        "Vertices: {count}\n"
        "Min: {min:.6f} | Average: {average:.6f} | "
        "Median: {median:.6f} | Max: {max:.6f}"
    ).format(**stats)


def _show_stats(
    stats: Dict[str, float],
    control_key: str = "stats",
) -> None:
    control = UI.get(control_key)
    if control and cmds.control(control, exists=True):
        cmds.text(
            control,
            edit=True,
            label=_format_stats(stats),
        )


def _mirror_distances(
    target: str,
    reference: str,
    indices: Optional[Sequence[int]] = None,
    mode: str = "Total",
) -> Dict[int, float]:
    _validate_topology(target, reference)

    target_points = _world_points(target)
    reference_points = _world_points(reference)

    if indices is None:
        indices = range(int(MIRROR_MAP["vertex_count"]))

    mapping = MIRROR_MAP["mapping"]

    return {
        index: _axis_distance(
            target_points[index],
            reference_points[mapping[index]],
            mode,
        )
        for index in indices
    }


def _direct_distances(
    mesh_a: str,
    mesh_b: str,
    indices: Optional[Sequence[int]] = None,
    mode: str = "Total",
) -> Dict[int, float]:
    vertex_count = _validate_same_topology(mesh_a, mesh_b)

    points_a = _world_points(mesh_a)
    points_b = _world_points(mesh_b)

    if indices is None:
        indices = range(vertex_count)

    return {
        index: _axis_distance(
            points_a[index],
            points_b[index],
            mode,
        )
        for index in indices
    }


# -----------------------------------------------------------------------------
# Session progress
# -----------------------------------------------------------------------------


def _record_history(
    operation: str,
    mode: str,
    stats: Dict[str, float],
    scope: str,
) -> None:
    SESSION_HISTORY.append(
        {
            "timestamp": datetime.now().isoformat(
                timespec="seconds"
            ),
            "operation": operation,
            "mode": mode,
            "scope": scope,
            **stats,
        }
    )
    _refresh_history_list()


def _refresh_history_list() -> None:
    control = UI.get("history_list")
    if not control or not cmds.control(control, exists=True):
        return

    cmds.textScrollList(
        control,
        edit=True,
        removeAll=True,
    )

    for row in SESSION_HISTORY:
        label = (
            "{timestamp} | {operation} | {mode} | {scope} | "
            "{count} verts | avg {average:.6f} | max {max:.6f}"
        ).format(**row)

        cmds.textScrollList(
            control,
            edit=True,
            append=label,
        )


@_guard
def clear_history(*_args) -> None:
    SESSION_HISTORY[:] = []
    _refresh_history_list()
    _set_status("Session history cleared.", True)


@_guard
def export_history(*_args) -> None:
    if not SESSION_HISTORY:
        raise RuntimeError("The session history is empty.")

    result = cmds.fileDialog2(
        fileMode=0,
        caption="Export Session History",
        fileFilter="CSV (*.csv)",
    )

    if not result:
        return

    path = result[0]
    if not path.lower().endswith(".csv"):
        path += ".csv"

    fields = [
        "timestamp",
        "operation",
        "mode",
        "scope",
        "count",
        "min",
        "average",
        "median",
        "max",
    ]

    with open(
        path,
        "w",
        newline="",
        encoding="utf-8",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(SESSION_HISTORY)

    _set_status(
        f"Session history exported: {os.path.basename(path)}",
        True,
    )


# -----------------------------------------------------------------------------
# Stable mirror snap
# -----------------------------------------------------------------------------


def _perform_snap() -> None:
    target, indices, components = _selected_vertices(
        required=True
    )
    reference = _reference_mesh()

    _validate_topology(target, reference)

    blend, use_x, use_y, use_z, enabled_axes = _snap_options()

    target_points = _world_points(target)
    reference_points = _world_points(reference)
    mapping = MIRROR_MAP["mapping"]

    before_distances = [
        _axis_distance(
            target_points[index],
            reference_points[mapping[index]],
            "Total",
        )
        for index in indices
    ]

    cmds.undoInfo(
        openChunk=True,
        chunkName="MetaHumanMirrorToolkitSnap",
    )

    try:
        for index, component in zip(indices, components):
            current = target_points[index]
            source = reference_points[mapping[index]]

            x = (
                current.x
                + ((source.x - current.x) * blend)
                if use_x
                else current.x
            )
            y = (
                current.y
                + ((source.y - current.y) * blend)
                if use_y
                else current.y
            )
            z = (
                current.z
                + ((source.z - current.z) * blend)
                if use_z
                else current.z
            )

            cmds.xform(
                component,
                worldSpace=True,
                translation=(x, y, z),
            )
    finally:
        cmds.undoInfo(closeChunk=True)

    cmds.select(
        components,
        replace=True,
    )

    stats = _statistics(before_distances)
    _show_stats(stats)

    _record_history(
        operation="Snap",
        mode=enabled_axes,
        stats=stats,
        scope="Selection",
    )

    _set_status(
        f"{len(components)} vertices snapped "
        f"(Blend {blend:.3f}, Axes {enabled_axes}).",
        True,
    )


@_guard
def snap_selected(*_args) -> None:
    _perform_snap()



# -----------------------------------------------------------------------------
# Direct same-topology transfer
# -----------------------------------------------------------------------------


def _snap_options() -> Tuple[float, bool, bool, bool, str]:
    blend = cmds.floatSliderGrp(
        UI["blend"],
        query=True,
        value=True,
    )

    use_x = cmds.checkBox(
        UI["axis_x"],
        query=True,
        value=True,
    )
    use_y = cmds.checkBox(
        UI["axis_y"],
        query=True,
        value=True,
    )
    use_z = cmds.checkBox(
        UI["axis_z"],
        query=True,
        value=True,
    )

    if not any((use_x, use_y, use_z)):
        raise RuntimeError("Enable at least one axis: X, Y or Z.")

    enabled_axes = "".join(
        axis
        for axis, enabled in (
            ("X", use_x),
            ("Y", use_y),
            ("Z", use_z),
        )
        if enabled
    )

    return blend, use_x, use_y, use_z, enabled_axes


def _perform_direct_snap() -> None:
    """
    Copy selected target vertex positions from the same vertex indices on the
    configured Source / Compare Mesh.

    Example:
        Target.vtx[123] <- Source.vtx[123]

    Both meshes must be non-mirrored and must share the same vertex count.
    Same vertex order is assumed.
    """
    target, indices, components = _selected_vertices(
        required=True
    )
    source = _compare_mesh()

    _validate_same_topology(target, source)

    blend, use_x, use_y, use_z, enabled_axes = _snap_options()

    target_points = _world_points(target)
    source_points = _world_points(source)

    before_distances = [
        _axis_distance(
            target_points[index],
            source_points[index],
            "Total",
        )
        for index in indices
    ]

    cmds.undoInfo(
        openChunk=True,
        chunkName="MetaHumanMirrorToolkitDirectSnap",
    )

    try:
        for index, component in zip(indices, components):
            current = target_points[index]
            source_point = source_points[index]

            x = (
                current.x
                + ((source_point.x - current.x) * blend)
                if use_x
                else current.x
            )
            y = (
                current.y
                + ((source_point.y - current.y) * blend)
                if use_y
                else current.y
            )
            z = (
                current.z
                + ((source_point.z - current.z) * blend)
                if use_z
                else current.z
            )

            cmds.xform(
                component,
                worldSpace=True,
                translation=(x, y, z),
            )
    finally:
        cmds.undoInfo(closeChunk=True)

    cmds.select(
        components,
        replace=True,
    )

    stats = _statistics(before_distances)
    _show_stats(stats)

    _record_history(
        operation="Direct Snap",
        mode=enabled_axes,
        stats=stats,
        scope="Selection",
    )

    _set_status(
        f"{len(components)} vertices copied from Source / Compare Mesh "
        f"(Blend {blend:.3f}, Axes {enabled_axes}).",
        True,
    )


@_guard
def direct_snap_selected(*_args) -> None:
    _perform_direct_snap()


# -----------------------------------------------------------------------------
# Mirror analysis
# -----------------------------------------------------------------------------


def _analysis_scope() -> Tuple[
    str,
    Optional[List[int]],
    str,
]:
    selected_mesh, indices, _components = _selected_vertices(
        required=False
    )

    if selected_mesh and indices:
        return selected_mesh, indices, "Selection"

    return _target_mesh(), None, "Full Mesh"


@_guard
def analyse_mirror(*_args) -> None:
    target, indices, scope = _analysis_scope()
    reference = _reference_mesh()
    mode = _mode_value("analysis_mode")

    distances = _mirror_distances(
        target=target,
        reference=reference,
        indices=indices,
        mode=mode,
    )

    stats = _statistics(distances.values())
    _show_stats(stats)

    _record_history(
        operation="Mirror Analysis",
        mode=mode,
        stats=stats,
        scope=scope,
    )

    _set_status(
        f"Mirror analysis complete "
        f"({mode}, {scope}, {stats['count']} vertices).",
        True,
    )


@_guard
def select_above_threshold(*_args) -> None:
    target = _target_mesh()
    reference = _reference_mesh()
    mode = _mode_value("analysis_mode")

    threshold = cmds.floatFieldGrp(
        UI["threshold"],
        query=True,
        value1=True,
    )

    distances = _mirror_distances(
        target=target,
        reference=reference,
        indices=None,
        mode=mode,
    )

    components = [
        f"{target}.vtx[{index}]"
        for index, distance in distances.items()
        if distance > threshold
    ]

    if components:
        cmds.select(
            components,
            replace=True,
        )
    else:
        cmds.select(clear=True)

    _set_status(
        f"{len(components)} vertices selected above "
        f"{threshold:.6f} ({mode}).",
        True,
    )


# -----------------------------------------------------------------------------
# Same-topology mesh comparison
# -----------------------------------------------------------------------------


@_guard
def compare_meshes(*_args) -> None:
    global COMPARE_ROWS

    target = _target_mesh()
    compare = _compare_mesh()
    mode = _mode_value("compare_mode")

    top_count = cmds.intFieldGrp(
        UI["top_count"],
        query=True,
        value1=True,
    )

    distances = _direct_distances(
        mesh_a=target,
        mesh_b=compare,
        indices=None,
        mode=mode,
    )

    stats = _statistics(distances.values())
    _show_stats(
        stats,
        control_key="compare_stats",
    )

    COMPARE_ROWS = sorted(
        distances.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:max(1, top_count)]

    cmds.textScrollList(
        UI["compare_list"],
        edit=True,
        removeAll=True,
    )

    for index, distance in COMPARE_ROWS:
        cmds.textScrollList(
            UI["compare_list"],
            edit=True,
            append=(
                f"vtx[{index}]  |  "
                f"{mode}: {distance:.6f}"
            ),
        )

    _record_history(
        operation="Mesh Compare",
        mode=mode,
        stats=stats,
        scope="Full Mesh",
    )

    _set_status(
        f"Mesh comparison complete "
        f"({mode}, {stats['count']} vertices).",
        True,
    )


@_guard
def select_compare_rows(*_args) -> None:
    selected_rows = cmds.textScrollList(
        UI["compare_list"],
        query=True,
        selectIndexedItem=True,
    ) or []

    if not selected_rows:
        return

    target = _target_mesh()

    components = [
        f"{target}.vtx[{COMPARE_ROWS[row - 1][0]}]"
        for row in selected_rows
    ]

    cmds.select(
        components,
        replace=True,
    )


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------


def _add_mode_menu(
    key: str,
    label: str,
) -> str:
    UI[key] = cmds.optionMenuGrp(label=label)

    for item in ("Total", "X", "Y", "Z"):
        cmds.menuItem(label=item)

    return UI[key]


def show() -> None:
    if cmds.window(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME)

    # Reloading keeps development iterations convenient in Maya.
    import importlib
    importlib.reload(mesh_revision_manager)
    mesh_revision_manager.configure(
        target_mesh_getter=_target_mesh,
        status_callback=_set_status,
    )

    cmds.window(
        WINDOW_NAME,
        title=WINDOW_TITLE,
        sizeable=True,
        widthHeight=(700, 660),
    )

    root = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=6,
    )

    tabs = cmds.tabLayout(
        innerMarginWidth=8,
        innerMarginHeight=8,
    )

    # Setup
    setup_tab = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
    )

    cmds.frameLayout(
        label="Configuration",
        collapsable=False,
        marginWidth=10,
        marginHeight=10,
    )
    cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=7,
    )

    UI["map"] = cmds.textFieldButtonGrp(
        label="Mirror Map JSON",
        buttonLabel="Load",
        buttonCommand=browse_mirror_map,
        adjustableColumn=2,
    )

    UI["reference"] = cmds.textFieldButtonGrp(
        label="Reference Mesh (Mirrored)",
        buttonLabel="Use Selection",
        buttonCommand=_guard(
            lambda *_: _set_mesh_from_selection("reference")
        ),
        adjustableColumn=2,
    )

    UI["target"] = cmds.textFieldButtonGrp(
        label="Target Mesh",
        buttonLabel="Use Selection",
        buttonCommand=_guard(
            lambda *_: _set_mesh_from_selection("target")
        ),
        adjustableColumn=2,
    )

    UI["compare"] = cmds.textFieldButtonGrp(
        label="Source / Compare Mesh",
        buttonLabel="Use Selection",
        buttonCommand=_guard(
            lambda *_: _set_mesh_from_selection("compare")
        ),
        adjustableColumn=2,
    )

    cmds.text(
        label=(
            "Reference Mesh (Mirrored): the reference copy displayed with "
            "a negative X scale.\n"
            "Target Mesh: the mesh being edited and analysed.\n"
            "Source / Compare Mesh: a non-mirrored same-topology mesh used for "
            "direct vertex transfer and version comparison."
        ),
        align="left",
        wordWrap=True,
    )

    cmds.setParent(tabs)

    # Snap
    snap_tab = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
    )

    cmds.frameLayout(
        label="Mirror Snap",
        collapsable=False,
        marginWidth=10,
        marginHeight=10,
    )
    cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=7,
    )

    UI["blend"] = cmds.floatSliderGrp(
        label="Blend",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        value=1.0,
        precision=3,
    )

    cmds.rowLayout(
        numberOfColumns=4,
        adjustableColumn=4,
    )
    cmds.text(label="Axes:")
    UI["axis_x"] = cmds.checkBox(
        label="X",
        value=True,
    )
    UI["axis_y"] = cmds.checkBox(
        label="Y",
        value=True,
    )
    UI["axis_z"] = cmds.checkBox(
        label="Z",
        value=True,
    )
    cmds.setParent("..")

    cmds.button(
        label="Mirror Snap Selected",
        height=44,
        command=snap_selected,
    )

    cmds.separator(height=12, style="in")

    cmds.text(
        label=(
            "Direct Snap copies the same vertex indices from the configured "
            "Source / Compare Mesh. Use it to transfer ears, nose, lips, or "
            "any selected region between same-topology MetaHuman meshes."
        ),
        align="left",
        wordWrap=True,
    )

    cmds.button(
        label="Direct Snap Selected From Source Mesh",
        height=44,
        command=direct_snap_selected,
    )

    UI["stats"] = cmds.text(
        label="No statistics yet.",
        align="left",
        height=44,
    )

    cmds.setParent(tabs)

    # Analysis
    analysis_tab = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
    )

    cmds.frameLayout(
        label="Mirror Analysis",
        collapsable=False,
        marginWidth=10,
        marginHeight=10,
    )
    cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=7,
    )

    cmds.text(
        label=(
            "With selected Target Mesh vertices, analysis uses the selection.\n"
            "Without vertex selection, analysis uses the full configured "
            "Target Mesh."
        ),
        align="left",
        wordWrap=True,
    )

    _add_mode_menu(
        "analysis_mode",
        "Distance Mode",
    )

    cmds.button(
        label="Analyse Distances",
        command=analyse_mirror,
    )

    UI["threshold"] = cmds.floatFieldGrp(
        label="Selection Threshold",
        numberOfFields=1,
        value1=0.5,
        precision=6,
    )

    cmds.button(
        label="Select Above Threshold",
        command=select_above_threshold,
    )

    cmds.setParent(tabs)

    # Compare
    compare_tab = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
    )

    cmds.frameLayout(
        label="Same-Topology Mesh Compare",
        collapsable=False,
        marginWidth=10,
        marginHeight=10,
    )
    cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=7,
    )

    cmds.text(
        label=(
            "Target Mesh and Source / Compare Mesh must be non-mirrored, share "
            "the same vertex count, and use the same vertex order "
            "(assumed)."
        ),
        align="left",
        wordWrap=True,
    )

    _add_mode_menu(
        "compare_mode",
        "Distance Mode",
    )

    UI["top_count"] = cmds.intFieldGrp(
        label="Largest Differences",
        numberOfFields=1,
        value1=20,
    )

    cmds.button(
        label="Compare Meshes",
        command=compare_meshes,
    )

    UI["compare_stats"] = cmds.text(
        label="No comparison yet.",
        align="left",
        height=44,
    )

    UI["compare_list"] = cmds.textScrollList(
        numberOfRows=14,
        allowMultiSelection=True,
        selectCommand=select_compare_rows,
    )

    cmds.setParent(tabs)

    # Revisions
    revisions_tab = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
    )

    mesh_revision_manager.build_ui(
        parent=revisions_tab,
    )

    cmds.setParent(tabs)

    # Progress
    progress_tab = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
    )

    cmds.frameLayout(
        label="Session Progress",
        collapsable=False,
        marginWidth=10,
        marginHeight=10,
    )
    cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=7,
    )

    cmds.text(
        label=(
            "Snap, analysis and mesh-comparison operations are recorded "
            "for the current Maya session.\n"
            "History is stored in memory until Maya closes or Clear History "
            "is used."
        ),
        align="left",
        wordWrap=True,
    )

    UI["history_list"] = cmds.textScrollList(
        numberOfRows=20,
        allowMultiSelection=False,
    )

    cmds.rowLayout(
        numberOfColumns=2,
        adjustableColumn=1,
    )
    cmds.button(
        label="Export CSV",
        command=export_history,
    )
    cmds.button(
        label="Clear History",
        command=clear_history,
    )
    cmds.setParent("..")

    cmds.setParent(tabs)

    cmds.tabLayout(
        tabs,
        edit=True,
        tabLabel=(
            (setup_tab, "Setup"),
            (snap_tab, "Snap"),
            (analysis_tab, "Analysis"),
            (compare_tab, "Compare"),
            (revisions_tab, "Revisions"),
            (progress_tab, "Progress"),
        ),
    )

    cmds.setParent(root)

    UI["status"] = cmds.text(
        label=f"Ready - v{VERSION}",
        align="left",
        height=38,
        backgroundColor=(0.25, 0.25, 0.25),
    )

    cmds.showWindow(WINDOW_NAME)


if __name__ == "__main__":
    show()
