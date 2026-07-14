# MetaHuman Mirror Toolkit

A toolkit for Autodesk Maya designed to speed up sculpting workflows on MetaHuman head meshes sharing the official Epic Games topology.

> **Status:** v1.1.0

## Why this project?

MetaHuman Mirror Toolkit was originally developed to solve a repetitive production problem encountered during the creation of a personal project.

As the toolkit proved useful in daily production, it was decided to make it publicly available so that other artists and developers could benefit from it and contribute to its evolution.

The primary goal of this project is to provide practical production tools rather than experimental features.

## Project Status

MetaHuman Mirror Toolkit is actively developed and maintained alongside other personal projects.

While the core features are stable and used regularly, the toolkit is still evolving and new features will continue to be added and refined over time.

## Features

- Mirror vertex snapping using a validated mirror map.
- Direct vertex transfer between two MetaHuman meshes sharing the same topology.
- Adjustable blend factor.
- Per-axis transfer (X / Y / Z).
- Mirror distance analysis.
- Threshold-based vertex selection.
- Same-topology mesh comparison.
- Session progress history.
- CSV export.
- Mesh revision manager.

## Requirements

- Autodesk Maya 2024+ (Python 3)
- Epic MetaHuman `head_lod0_mesh`
- Same vertex count required; same vertex order assumed.

## Installation

1. Copy `metahuman_mirror_toolkit.py` and `mesh_revision_manager.py`.
2. Launch Maya.
3. Run:

```python
import importlib
import metahuman_mirror_toolkit

importlib.reload(metahuman_mirror_toolkit)
metahuman_mirror_toolkit.show()
```

4. Load the provided Mirror Map JSON.

## Workflow

### Mirror Snap

Uses the Mirror Map:

```text
Target Vertex
      |
      v
Mirror Map
      |
      v
Reference Mesh (Mirrored)
```

### Direct Snap

Copies the same vertex indices between two MetaHuman meshes.

```text
Target.vtx[123] <- Source.vtx[123]
```

Useful for transferring ears, lips, noses or any sculpted region.

## Limitations

- Built for MetaHuman `head_lod0_mesh`.
- Mirror operations require the supplied mirror map or a compatible validated map.
- Direct Snap and Compare require the same vertex count.
- Same vertex order is assumed for Direct Snap and Compare. it is not verified automatically.

## Roadmap

Planned ideas (not implemented yet):

- Better visualization tools
- Other FaceMesh type support
- Advanced comparison tools

## Contributing

Bug fixes, performance improvements and workflow-oriented features are always welcome.

Please keep contributions:

- Focused
- Documented
- Backward compatible whenever possible

## License

MIT

## Disclaimer

This project is an independent tool and is not affiliated with, endorsed by, or sponsored by Epic Games or Autodesk.

MetaHuman is a trademark or registered trademark of Epic Games, Inc.

Autodesk and Maya are registered trademarks or trademarks of Autodesk, Inc., and/or its subsidiaries and/or affiliates in the United States and/or other countries.
