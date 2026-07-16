# -*- coding: utf-8 -*-
"""Undoable Maya command used by the mesh revision manager."""

from __future__ import annotations

from typing import List, Optional

import maya.api.OpenMaya as om


COMMAND_NAME = "mhBulkSetPoints"


def maya_useNewAPI() -> None:
    """Tell Maya that this plug-in uses Python API 2.0 objects."""
    pass


def _mesh_path(node: str) -> om.MDagPath:
    selection = om.MSelectionList()
    selection.add(node)
    path = selection.getDagPath(0)

    if path.hasFn(om.MFn.kTransform):
        path.extendToShape()

    if not path.hasFn(om.MFn.kMesh):
        raise RuntimeError(f"Node is not a mesh: {node}")

    return path


def _parse_indices(value: Optional[str], vertex_count: int) -> List[int]:
    if not value:
        return list(range(vertex_count))

    indices = []
    seen = set()

    for token in value.split(","):
        token = token.strip()
        if not token:
            continue

        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise RuntimeError(f"Invalid vertex range: {token}")
            values = range(start, end + 1)
        else:
            values = (int(token),)

        for index in values:
            if index < 0 or index >= vertex_count:
                raise RuntimeError(f"Vertex index is out of range: {index}")
            if index not in seen:
                seen.add(index)
                indices.append(index)

    if not indices:
        raise RuntimeError("No vertex indices were provided.")

    return indices


class BulkSetPointsCommand(om.MPxCommand):
    """Copy indexed world-space positions with native Maya undo support."""

    TARGET_FLAG = "-t"
    TARGET_LONG_FLAG = "-target"
    SOURCE_FLAG = "-s"
    SOURCE_LONG_FLAG = "-source"
    INDICES_FLAG = "-i"
    INDICES_LONG_FLAG = "-indices"

    def __init__(self) -> None:
        super().__init__()
        self._target_path = None
        self._before_points = None
        self._after_points = None

    @staticmethod
    def creator():
        return BulkSetPointsCommand()

    @staticmethod
    def create_syntax() -> om.MSyntax:
        syntax = om.MSyntax()
        syntax.addFlag(
            BulkSetPointsCommand.TARGET_FLAG,
            BulkSetPointsCommand.TARGET_LONG_FLAG,
            om.MSyntax.kString,
        )
        syntax.addFlag(
            BulkSetPointsCommand.SOURCE_FLAG,
            BulkSetPointsCommand.SOURCE_LONG_FLAG,
            om.MSyntax.kString,
        )
        syntax.addFlag(
            BulkSetPointsCommand.INDICES_FLAG,
            BulkSetPointsCommand.INDICES_LONG_FLAG,
            om.MSyntax.kString,
        )
        return syntax

    def isUndoable(self) -> bool:
        return True

    def doIt(self, args: om.MArgList) -> None:
        arguments = om.MArgDatabase(self.syntax(), args)

        if not arguments.isFlagSet(self.TARGET_FLAG):
            raise RuntimeError("The target mesh is required.")
        if not arguments.isFlagSet(self.SOURCE_FLAG):
            raise RuntimeError("The source mesh is required.")

        target = arguments.flagArgumentString(self.TARGET_FLAG, 0)
        source = arguments.flagArgumentString(self.SOURCE_FLAG, 0)
        indices_value = (
            arguments.flagArgumentString(self.INDICES_FLAG, 0)
            if arguments.isFlagSet(self.INDICES_FLAG)
            else None
        )

        self._target_path = _mesh_path(target)
        source_path = _mesh_path(source)
        target_fn = om.MFnMesh(self._target_path)
        source_fn = om.MFnMesh(source_path)

        if target_fn.numVertices != source_fn.numVertices:
            raise RuntimeError(
                "The source and target meshes do not have the same vertex count."
            )

        indices = _parse_indices(indices_value, target_fn.numVertices)
        self._before_points = om.MPointArray(
            target_fn.getPoints(om.MSpace.kObject)
        )
        self._after_points = om.MPointArray(self._before_points)

        source_world_points = source_fn.getPoints(om.MSpace.kWorld)
        world_to_target = self._target_path.inclusiveMatrixInverse()

        for index in indices:
            self._after_points[index] = source_world_points[index] * world_to_target

        self.redoIt()
        self.setResult(len(indices))

    def redoIt(self) -> None:
        om.MFnMesh(self._target_path).setPoints(
            self._after_points,
            om.MSpace.kObject,
        )

    def undoIt(self) -> None:
        om.MFnMesh(self._target_path).setPoints(
            self._before_points,
            om.MSpace.kObject,
        )


def initializePlugin(plugin_object) -> None:
    plugin = om.MFnPlugin(plugin_object)
    plugin.registerCommand(
        COMMAND_NAME,
        BulkSetPointsCommand.creator,
        BulkSetPointsCommand.create_syntax,
    )


def uninitializePlugin(plugin_object) -> None:
    plugin = om.MFnPlugin(plugin_object)
    plugin.deregisterCommand(COMMAND_NAME)
