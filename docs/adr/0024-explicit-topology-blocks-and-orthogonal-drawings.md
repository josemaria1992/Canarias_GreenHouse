# ADR-0024: explicit hydraulic topology, functional blocks and orthogonal drawings (0.7.0)

Status: proposed for 0.7.0 (owner brief and the owner's routing request of 25 October 2026;
PIPE_ROUTING_AND_DIAGRAM_LAYOUT, EQUIPMENT_AND_HYDRAULIC_MODEL). Supersedes, for new drawings, the 0.5.6 layout
as a source of water connectivity; the 0.5.6 layout stays as a read-only reference and for the fixed installation.

## Context

Up to 0.6.13 the twin's water followed the drawing: `twin/water.py::Network` decided that a valve "sits on" a pipe
from its distance, that two pipes join when an end lies near the other, and pipes were free polylines. Tidying a
drawing could therefore change the simulated process, and a crossing could join two pipes. The owner asked for
pipes with horizontal and vertical segments only, automatic routing, draggable segments and clean crossings.

## Decision

1. **Graph** (`topology/`): nodes are equipment items (one item is one node, whatever the number of drawings) or
   junctions; ports come from the item type's water kind (`TypeBody.hydraulic`: source, tank, pump, valve, meter,
   sensor, sector), with a direction (in, out, both) and whether a port takes several pipes; edges join a port
   water may leave by to a port it may enter by; sensors are associated with the tank or pipe they measure (a
   tap carries no water). The graph's digest covers only this. Checks return structured issues (rule, object,
   message, fix) and prove structure only.
2. **Passive equipment**: tanks, water sources and sectors are equipment types with no channel role
   (TypeBody may now have zero roles if its water kind is passive). Their numbers are item properties with a
   provenance; unknown values stay unset.
3. **Drawing** (`diagram.py`): symbols placed for nodes and routes drawn for edges, per view, as presentation data
   with its own digest. A route's consecutive points differ in exactly one coordinate; its ends sit on the ports'
   anchors (computed identically in Python and in the browser); a route that doubles back, a diagonal, a detached
   end or a non-finite point is refused on save; overlaps of unrelated pipes are flagged, never merged.
4. **Routing** (`web/router.js`, pure, tested under Node): stubs out of each port in the direction it faces,
   direct/L/Z candidates, then A* on a sparse rectilinear grid (state includes direction; bend and crossing
   costs; per-row and per-column blocked intervals and crossing positions), bounded by states and time. No
   diagonal fallback: no route is a refusal with its reason. A dragged segment becomes a pin the router keeps
   while feasible ("Reroute selected" removes it). Crossings render as a rectangular gap in the pipe whose id
   sorts later; an explicit junction renders as a dot. Junctions are created deliberately ("Junction" splits a
   pipe into two edges with lineage).
5. **Blocks** (`blocks/`): versioned DRAFT/VALIDATED/RETIRED groups of existing nodes with a supply, a
   destination, an optional inline meter on their path and an operating plan (startup, normal stop, fault stop)
   from an editable template. Validation proves that an explicit path joins supply and destination through the
   members, that a meter is on that path, and that both stop plans stop what the startup starts. A block whose
   graph digest changed is marked for revalidation. Membership reserves nothing.
6. **Migration** (`topology/migrate.py`): an old drawing's explicit `from_node`/`to_node` links become a draft;
   everything the old model inferred from geometry is listed as ambiguous and never connected. Nothing is
   saved until a person saves the reviewed draft; the old layout record is untouched.
7. **Persistence** (schema 6): `topology_revisions`, `diagram_revisions` and `blocks`, all with expected-revision
   checks; the editor saves graph and drawing together after checking both.

## Consequences

* Moving or rerouting never changes the graph digest; a Join or a reconnect does, and invalidates the blocks
  and twin proofs that depend on it.
* The fixed-installation twin (ADR-0013/0014) keeps its own model and drawing; the new graph water model (ADR-0026)
  runs on the explicit graph only.
