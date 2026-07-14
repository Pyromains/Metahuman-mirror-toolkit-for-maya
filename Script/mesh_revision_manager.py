# -*- coding: utf-8 -*-
"""
Mesh Revision Manager
=====================

Companion module for MetaHuman Mirror Toolkit.

It stores append-only OBJ snapshots of a configured Target Mesh next to the
current Maya scene and records revision metadata in JSON.

Directory layout
----------------
<scene directory>/
    <scene name>_mesh_revisions/
        <target mesh name>/
            <target mesh name>_v001.obj
            <target mesh name>_v002.obj
            revisions.json

The module never overwrites an existing revision.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds


MODULE_VERSION = "1.0.0"
UI: Dict[str, str] = {}
_CONTEXT: Dict[str, Callable] = {}


# -----------------------------------------------------------------------------
# Context and helpers
# -----------------------------------------------------------------------------


def configure(
    target_mesh_getter: Callable[[], str],
    status_callback: Optional[Callable[[str, bool], None]] = None,
) -> None:
    """Provide callbacks owned by the main toolkit."""
    _CONTEXT["target_mesh_getter"] = target_mesh_getter
    if status_callback:
        _CONTEXT["status_callback"] = status_callback


def _status(message: str, success: bool = False) -> None:
    print(f"[Mesh Revision Manager] {message}")

    callback = _CONTEXT.get("status_callback")
    if callback:
        callback(message, success)


def _guard(callback):
    def wrapped(*args):
        try:
            return callback(*args)
        except Exception as exc:
            cmds.warning(str(exc))
            _status(str(exc), False)
            return None

    return wrapped


def _target_mesh() -> str:
    getter = _CONTEXT.get("target_mesh_getter")
    if not getter:
        raise RuntimeError(
            "Mesh Revision Manager has not been configured by the main toolkit."
        )

    mesh = getter()
    if not mesh or not cmds.objExists(mesh):
        raise RuntimeError("Set a valid Target Mesh first.")

    return _as_transform(mesh)


def _as_transform(node: str) -> str:
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
        raise RuntimeError("The target node does not contain a mesh.")

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


def _safe_name(name: str) -> str:
    short_name = name.split("|")[-1].replace(":", "_")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", short_name).strip("_")
    return safe or "mesh"


def _scene_path() -> Path:
    scene = cmds.file(query=True, sceneName=True)

    if not scene:
        raise RuntimeError(
            "Save the Maya scene before creating a mesh revision."
        )

    return Path(scene)


def _revision_paths(target_mesh: str) -> Tuple[Path, Path]:
    scene = _scene_path()
    target_name = _safe_name(target_mesh)

    revision_dir = (
        scene.parent
        / f"{scene.stem}_mesh_revisions"
        / target_name
    )
    metadata_path = revision_dir / "revisions.json"

    return revision_dir, metadata_path


def _default_metadata(
    scene: Path,
    target_mesh: str,
) -> Dict:
    return {
        "schema_version": 1,
        "manager_version": MODULE_VERSION,
        "scene": scene.name,
        "scene_path": str(scene),
        "target_mesh": target_mesh.split("|")[-1],
        "export_format": "obj",
        "revisions": [],
    }


def _load_metadata(
    target_mesh: str,
    create_if_missing: bool = False,
) -> Tuple[Dict, Path, Path]:
    scene = _scene_path()
    revision_dir, metadata_path = _revision_paths(target_mesh)

    if metadata_path.exists():
        with metadata_path.open("r", encoding="utf-8") as stream:
            metadata = json.load(stream)
    else:
        metadata = _default_metadata(scene, target_mesh)
        if create_if_missing:
            revision_dir.mkdir(parents=True, exist_ok=True)

    revisions = metadata.setdefault("revisions", [])
    if not isinstance(revisions, list):
        raise RuntimeError("Invalid revisions.json: 'revisions' must be a list.")

    return metadata, revision_dir, metadata_path


def _write_metadata(metadata: Dict, metadata_path: Path) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = metadata_path.with_suffix(".json.tmp")

    with temporary_path.open("w", encoding="utf-8") as stream:
        json.dump(
            metadata,
            stream,
            indent=2,
            ensure_ascii=False,
        )

    os.replace(str(temporary_path), str(metadata_path))


def _next_revision_number(
    metadata: Dict,
    revision_dir: Path,
    target_name: str,
) -> int:
    numbers = {
        int(item.get("version", 0))
        for item in metadata.get("revisions", [])
        if str(item.get("version", "")).isdigit()
    }

    pattern = re.compile(
        rf"^{re.escape(target_name)}_v(\d+)\.obj$",
        re.IGNORECASE,
    )

    if revision_dir.exists():
        for path in revision_dir.iterdir():
            match = pattern.match(path.name)
            if match:
                numbers.add(int(match.group(1)))

    return max(numbers, default=0) + 1


def _ensure_obj_exporter() -> None:
    if not cmds.pluginInfo(
        "objExport",
        query=True,
        loaded=True,
    ):
        try:
            cmds.loadPlugin("objExport", quiet=True)
        except Exception as exc:
            raise RuntimeError(
                "Maya OBJ Export plug-in could not be loaded."
            ) from exc


def _export_target_as_obj(
    target_mesh: str,
    filepath: Path,
) -> None:
    _ensure_obj_exporter()

    previous_selection = cmds.ls(
        selection=True,
        long=True,
    ) or []

    try:
        cmds.select(target_mesh, replace=True)

        cmds.file(
            str(filepath),
            force=False,
            options=(
                "groups=0;"
                "ptgroups=0;"
                "materials=0;"
                "smoothing=1;"
                "normals=1"
            ),
            typ="OBJexport",
            preserveReferences=True,
            exportSelected=True,
        )
    finally:
        if previous_selection:
            cmds.select(previous_selection, replace=True)
        else:
            cmds.select(clear=True)


# -----------------------------------------------------------------------------
# Revision operations
# -----------------------------------------------------------------------------


def create_revision(comment: str) -> Dict:
    target_mesh = _target_mesh()
    target_name = _safe_name(target_mesh)

    metadata, revision_dir, metadata_path = _load_metadata(
        target_mesh,
        create_if_missing=True,
    )

    revision_number = _next_revision_number(
        metadata,
        revision_dir,
        target_name,
    )

    filename = f"{target_name}_v{revision_number:03d}.obj"
    filepath = revision_dir / filename

    if filepath.exists():
        raise RuntimeError(
            f"Revision file already exists and will not be overwritten: {filename}"
        )

    vertex_count = _mesh_fn(target_mesh).numVertices

    _export_target_as_obj(
        target_mesh=target_mesh,
        filepath=filepath,
    )

    record = {
        "version": revision_number,
        "file": filename,
        "created_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "comment": comment.strip(),
        "vertex_count": vertex_count,
        "scene": _scene_path().name,
        "target_mesh": target_mesh.split("|")[-1],
    }

    metadata["manager_version"] = MODULE_VERSION
    metadata["scene"] = _scene_path().name
    metadata["scene_path"] = str(_scene_path())
    metadata["target_mesh"] = target_mesh.split("|")[-1]
    metadata["revisions"].append(record)

    _write_metadata(metadata, metadata_path)

    return record


def list_revisions() -> List[Dict]:
    target_mesh = _target_mesh()
    metadata, _revision_dir, _metadata_path = _load_metadata(
        target_mesh,
        create_if_missing=False,
    )

    return sorted(
        metadata.get("revisions", []),
        key=lambda row: int(row.get("version", 0)),
        reverse=True,
    )


def _selected_revision() -> Dict:
    selected = cmds.textScrollList(
        UI["revision_list"],
        query=True,
        selectIndexedItem=True,
    ) or []

    if not selected:
        raise RuntimeError("Select a revision first.")

    revisions = list_revisions()
    index = selected[0] - 1

    if index < 0 or index >= len(revisions):
        raise RuntimeError("The selected revision is no longer available.")

    return revisions[index]


def _import_revision_mesh(filepath: Path) -> str:
    if not filepath.exists():
        raise RuntimeError(
            f"Revision file not found: {filepath.name}"
        )

    before = set(
        cmds.ls(long=True, assemblies=True) or []
    )

    cmds.file(
        str(filepath),
        i=True,
        type="OBJ",
        ignoreVersion=True,
        mergeNamespacesOnClash=False,
        namespace="MHRevisionTemp",
        options="mo=0",
        preserveReferences=True,
        returnNewNodes=False,
    )

    after = set(
        cmds.ls(long=True, assemblies=True) or []
    )

    imported_roots = [
        node for node in (after - before)
        if cmds.objExists(node)
    ]

    imported_meshes = []

    for root in imported_roots:
        shapes = cmds.listRelatives(
            root,
            allDescendents=True,
            shapes=True,
            noIntermediate=True,
            fullPath=True,
        ) or []

        if cmds.nodeType(root) == "transform":
            direct_shapes = cmds.listRelatives(
                root,
                shapes=True,
                noIntermediate=True,
                fullPath=True,
            ) or []
            shapes.extend(direct_shapes)

        if any(cmds.nodeType(shape) == "mesh" for shape in shapes):
            imported_meshes.append(root)

    if len(imported_meshes) != 1:
        if imported_roots:
            cmds.delete(imported_roots)
        raise RuntimeError(
            "The revision OBJ must contain exactly one mesh."
        )

    return _as_transform(imported_meshes[0])


def restore_revision(
    revision: Dict,
    selected_only: bool = False,
) -> int:
    target_mesh = _target_mesh()
    metadata, revision_dir, _metadata_path = _load_metadata(
        target_mesh,
        create_if_missing=False,
    )

    filepath = revision_dir / revision["file"]
    imported_mesh = _import_revision_mesh(filepath)

    try:
        target_fn = _mesh_fn(target_mesh)
        imported_fn = _mesh_fn(imported_mesh)

        if target_fn.numVertices != imported_fn.numVertices:
            raise RuntimeError(
                "The revision mesh and Target Mesh do not have the same "
                "vertex count."
            )

        target_points = list(
            target_fn.getPoints(om.MSpace.kWorld)
        )
        imported_points = list(
            imported_fn.getPoints(om.MSpace.kWorld)
        )

        if selected_only:
            selected_mesh, indices, components = _selected_target_vertices(
                target_mesh
            )
        else:
            indices = list(range(target_fn.numVertices))
            components = [
                f"{target_mesh}.vtx[{index}]"
                for index in indices
            ]

        cmds.undoInfo(
            openChunk=True,
            chunkName="RestoreMeshRevision",
        )

        try:
            for index, component in zip(indices, components):
                point = imported_points[index]
                cmds.xform(
                    component,
                    worldSpace=True,
                    translation=(point.x, point.y, point.z),
                )
        finally:
            cmds.undoInfo(closeChunk=True)

        if selected_only:
            cmds.select(components, replace=True)
        else:
            cmds.select(target_mesh, replace=True)

        return len(indices)
    finally:
        if cmds.objExists(imported_mesh):
            cmds.delete(imported_mesh)

        if cmds.namespace(exists="MHRevisionTemp"):
            remaining = cmds.namespaceInfo(
                "MHRevisionTemp",
                listOnlyDependencyNodes=True,
            ) or []

            if not remaining:
                cmds.namespace(
                    removeNamespace="MHRevisionTemp",
                    mergeNamespaceWithRoot=True,
                )


def _selected_target_vertices(
    target_mesh: str,
) -> Tuple[str, List[int], List[str]]:
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
        raise RuntimeError(
            "Select Target Mesh vertices before restoring a region."
        )

    meshes = {
        component.split(".vtx[")[0]
        for component in components
    }

    if len(meshes) != 1:
        raise RuntimeError(
            "All selected vertices must belong to the same mesh."
        )

    selected_mesh = _as_transform(next(iter(meshes)))

    if selected_mesh != _as_transform(target_mesh):
        raise RuntimeError(
            "The selected vertices do not belong to the configured Target Mesh."
        )

    indices = [
        int(component.rsplit("[", 1)[1][:-1])
        for component in components
    ]

    return selected_mesh, indices, components


# -----------------------------------------------------------------------------
# UI callbacks
# -----------------------------------------------------------------------------


def _revision_label(row: Dict) -> str:
    comment = row.get("comment", "").replace("\n", " ").strip()
    if len(comment) > 70:
        comment = comment[:67] + "..."

    return (
        f"v{int(row.get('version', 0)):03d} | "
        f"{row.get('created_at', '')} | "
        f"{row.get('vertex_count', '?')} verts | "
        f"{comment or '(no comment)'}"
    )


def sync_with_target_mesh(silent: bool = True) -> None:
    """
    Refresh the revision UI for the currently configured Target Mesh.

    When silent is True, expected setup states such as an unsaved Maya scene
    or a mesh with no existing revisions simply reset the UI without warnings.
    """
    revision_list = UI.get("revision_list")
    latest_label = UI.get("latest")

    if not revision_list or not latest_label:
        return

    try:
        target_mesh = _target_mesh()
        _scene_path()
        _metadata, _revision_dir, metadata_path = _load_metadata(
            target_mesh,
            create_if_missing=False,
        )

        if not metadata_path.exists():
            revisions = []
        else:
            revisions = list_revisions()
    except Exception:
        if not silent:
            raise
        revisions = []

    cmds.textScrollList(
        revision_list,
        edit=True,
        removeAll=True,
    )

    for row in revisions:
        cmds.textScrollList(
            revision_list,
            edit=True,
            append=_revision_label(row),
        )

    latest = revisions[0]["version"] if revisions else 0

    cmds.text(
        latest_label,
        edit=True,
        label=(
            f"Latest revision: v{latest:03d}"
            if latest
            else "No revision saved yet."
        ),
    )


@_guard
def refresh_revisions(*_args) -> None:
    revisions = list_revisions()
    sync_with_target_mesh(silent=False)

    _status(
        f"Revision list refreshed: {len(revisions)} revision(s).",
        True,
    )


@_guard
def save_revision(*_args) -> None:
    comment = cmds.scrollField(
        UI["comment"],
        query=True,
        text=True,
    )

    record = create_revision(comment)

    cmds.scrollField(
        UI["comment"],
        edit=True,
        text="",
    )

    refresh_revisions()

    _status(
        f"Saved mesh revision v{record['version']:03d}: "
        f"{record['file']}",
        True,
    )


@_guard
def restore_full(*_args) -> None:
    revision = _selected_revision()
    count = restore_revision(
        revision,
        selected_only=False,
    )

    _status(
        f"Restored full mesh from v{revision['version']:03d} "
        f"({count} vertices).",
        True,
    )


@_guard
def restore_selected_region(*_args) -> None:
    revision = _selected_revision()
    count = restore_revision(
        revision,
        selected_only=True,
    )

    _status(
        f"Restored selected region from v{revision['version']:03d} "
        f"({count} vertices).",
        True,
    )


@_guard
def open_revision_folder(*_args) -> None:
    target_mesh = _target_mesh()
    revision_dir, _metadata_path = _revision_paths(target_mesh)
    revision_dir.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        os.startfile(str(revision_dir))
    elif cmds.about(mac=True):
        import subprocess
        subprocess.Popen(["open", str(revision_dir)])
    else:
        import subprocess
        subprocess.Popen(["xdg-open", str(revision_dir)])

    _status(f"Opened revision folder: {revision_dir}", True)


# -----------------------------------------------------------------------------
# UI construction
# -----------------------------------------------------------------------------


def build_ui(parent: Optional[str] = None) -> str:
    """
    Build the revision manager UI under the provided Maya layout.

    Returns the created root layout.
    """
    if parent:
        cmds.setParent(parent)

    root = cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=8,
    )

    cmds.frameLayout(
        label="Mesh Revision Manager",
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
            "Append-only OBJ snapshots are stored next to the current Maya "
            "scene. Existing revisions are never overwritten."
        ),
        align="left",
        wordWrap=True,
    )

    UI["latest"] = cmds.text(
        label="No revision saved yet.",
        align="left",
    )

    UI["comment"] = cmds.scrollField(
        wordWrap=True,
        height=86,
        text="",
    )

    cmds.button(
        label="Save New Mesh Revision",
        height=40,
        command=save_revision,
    )

    cmds.separator(
        height=10,
        style="in",
    )

    UI["revision_list"] = cmds.textScrollList(
        numberOfRows=15,
        allowMultiSelection=False,
    )

    cmds.rowLayout(
        numberOfColumns=2,
        adjustableColumn=1,
    )
    cmds.button(
        label="Refresh",
        command=refresh_revisions,
    )
    cmds.button(
        label="Open Revision Folder",
        command=open_revision_folder,
    )
    cmds.setParent("..")

    cmds.separator(
        height=10,
        style="in",
    )

    cmds.text(
        label=(
            "Restore Full Mesh replaces all Target Mesh vertex positions.\n"
            "Restore Selected Region only restores the currently selected "
            "Target Mesh vertices."
        ),
        align="left",
        wordWrap=True,
    )

    cmds.rowLayout(
        numberOfColumns=2,
        adjustableColumn=1,
    )
    cmds.button(
        label="Restore Full Mesh",
        command=restore_full,
    )
    cmds.button(
        label="Restore Selected Region",
        command=restore_selected_region,
    )
    cmds.setParent("..")

    cmds.setParent(root)

    return root
