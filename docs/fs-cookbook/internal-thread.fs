// FS COOKBOOK: internal (female) thread in a cylindrical hole.
//
// Builds on docs/fs-cookbook/helix.fs -- read that first. Same core trick
// (opFitSpline + opSweep, NOT opHelix, which is a reliable REGEN_ERROR
// source in agent-driven use). This recipe adds the three things a usable
// tapping feature needs:
//
//   1. RADIUS FROM SELECTION. Pick the hole's cylindrical FACE (radius +
//      axis + axial extent all come free from evSurfaceDefinition +
//      evBox3d) or the hole's inner circular EDGE (radius + axis from
//      evCurveDefinition; depth must then be typed in). No manual
//      diameter entry, no drift between hole and thread.
//   2. MULTI-START. `starts` N helices, each offset 2*pi*k/N in phase,
//      each advancing `lead = pitch * starts` per turn. Pitch stays
//      crest-to-crest (the number stamped on the fastener); lead is
//      derived. A 2-start M10x1.5 advances 3 mm per revolution.
//   3. CUT OUTWARD, NOT INWARD. This is the one sign flip that separates
//      an internal thread from helix.fs's external one. The hole wall as
//      drilled IS the minor diameter (the internal thread's crest). The
//      groove cuts radially OUTWARD from it to the major diameter. Get
//      this backwards and you cut into thin air inside the bore.
//
// GEOMETRY NOTES:
//   - ISO 60 deg metric: groove depth from the hole wall is
//     (D - D1)/2 = 0.5413 * P, and the sharp-V base width at the wall is
//     2 * depth * tan(30 deg) = 1.1547 * depth. Those are the `isoProfile`
//     defaults. Uncheck it to drive depth/width directly (Acme, trapezoidal,
//     buttress-ish, or a deliberately shallow print-friendly profile).
//   - The cut profile is pushed slightly PAST the hole wall (inward, into
//     the bore void) by `eps`, so the tool body properly protrudes instead
//     of sharing a coincident face with the wall. Coincident faces are the
//     classic source of "boolean succeeded but nothing was removed".
//     The flank slope is preserved when overshooting, so the groove is
//     still exactly `depth` deep measured from the wall.
//   - The helix overshoots one full lead past each end of the threaded
//     span, so the sweep clears the hole's end faces and leaves no slivers.
//   - `clearance` inflates the groove along the flank normal (radially by
//     `clearance`, axially by the matching 1.1547*clearance). Use it for
//     FDM/SLA where a nominal thread binds. 0.2-0.4 mm is a sane starting
//     point on a 0.4 mm nozzle; 0 for machined.
//
// FS PARSE TRAPS. Each one makes the Feature Studio compile to an EMPTY feature
// spec, and Onshape returns NO diagnostic whatsoever -- no line, no message.
// Note what this is NOT: it is not the prelude version. Onshape happily accepts
// an older `FeatureScript <N>;` (2909 and 2931 both compile against a 3008 std),
// so an empty spec always means syntax, never drift. Do not go chasing the
// version number like I did.
//   - There is no if-EXPRESSION. `var s = if (c) a else b;` does not parse.
//     Use an if/else STATEMENT. NB: helix.fs still carries this form, so it
//     no longer compiles as written on the current std library.
//   - There are no uninitialized declarations. `var x is ValueWithUnits;`
//     does not parse; every var needs a seed value.
//   - A Query parameter needs a "Filter" annotation; a bare
//     `annotation { "Name" : "x" } definition.x is Query;` does not parse.
//
// AND ONE RUNTIME TRAP:
//   - `transform(cSys)` does NOT build a Transform from a CoordSystem -- it
//     returns the CoordSystem unchanged. Use `toWorld(cSys)`.
//
// HANDEDNESS: the helix sign convention is inherited from helix.fs
// (render-verified there). The cylinder's own zAxis, however, can point
// either way down the bore depending on how the hole was modelled, so
// "right-handed" is defined in THAT frame. If a thread comes out mirrored,
// just toggle the checkbox -- that is cheaper than reasoning about which
// way evSurfaceDefinition decided to orient the axis.

FeatureScript 3008;
import(path : "onshape/std/geometry.fs", version : "3008.0");

export enum ThreadDepthMode
{
    annotation { "Name" : "Full selected face" }
    FULL_FACE,
    annotation { "Name" : "Specified depth" }
    SPECIFIED
}

annotation { "Feature Type Name" : "Internal Thread" }
export const internalThread = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "Hole wall (cylindrical face) or inner circle (edge)",
                     "Filter" : (EntityType.FACE && GeometryType.CYLINDER)
                             || (EntityType.EDGE && GeometryType.CIRCLE),
                     "MaxNumberOfPicks" : 1 }
        definition.seed is Query;

        annotation { "Name" : "Pitch (crest to crest)" }
        isLength(definition.pitch, LENGTH_BOUNDS);

        annotation { "Name" : "Number of starts" }
        isInteger(definition.starts, POSITIVE_COUNT_BOUNDS);

        annotation { "Name" : "Threaded length" }
        definition.depthMode is ThreadDepthMode;

        annotation { "Name" : "Depth" }
        if (definition.depthMode == ThreadDepthMode.SPECIFIED)
            isLength(definition.depth, LENGTH_BOUNDS);

        annotation { "Name" : "Flip direction" }
        if (definition.depthMode == ThreadDepthMode.SPECIFIED)
            definition.flip is boolean;

        annotation { "Name" : "Right-handed (uncheck for left-hand thread)" }
        definition.rightHanded is boolean;

        annotation { "Name" : "ISO 60 deg profile" }
        definition.isoProfile is boolean;

        annotation { "Name" : "Groove depth (radial, from hole wall)" }
        if (!definition.isoProfile)
            isLength(definition.profileDepth, LENGTH_BOUNDS);

        annotation { "Name" : "Groove width at hole wall (axial)" }
        if (!definition.isoProfile)
            isLength(definition.profileWidth, LENGTH_BOUNDS);

        // ZERO_INCLUSIVE_OFFSET_BOUNDS, not NONNEGATIVE_LENGTH_BOUNDS: despite
        // the name, the latter rejects an actual 0 ("Parameter clearance ...
        // does not match its feature spec"), and 0 is the correct default for
        // a machined thread.
        annotation { "Name" : "Radial clearance (3D printing fit)" }
        isLength(definition.clearance, ZERO_INCLUSIVE_OFFSET_BOUNDS);
    }
    {
        // ---- 1. Resolve radius, axis frame, and axial extent from the pick.
        const seed = definition.seed;
        if (size(evaluateQuery(context, seed)) == 0)
            throw regenError("Select the hole's cylindrical face, or its inner circular edge.", ["seed"]);

        const faceQ = qEntityFilter(seed, EntityType.FACE);
        const edgeQ = qEntityFilter(seed, EntityType.EDGE);
        const pickedFace = size(evaluateQuery(context, faceQ)) > 0;

        // NB: FeatureScript has no uninitialized declarations -- `var x is T;`
        // is a parse error (compiles to an empty feature spec, with no
        // diagnostic surfaced by the API). Every var gets a seed value.
        var cSys = coordSystem(vector(0, 0, 0) * meter, vector(1, 0, 0), vector(0, 0, 1));
        var rHole = 0 * meter;
        var faceLo is ValueWithUnits = 0 * meter;
        var faceHi is ValueWithUnits = 0 * meter;

        if (pickedFace)
        {
            const surf = evSurfaceDefinition(context, { "face" : faceQ });
            if (!(surf is Cylinder))
                throw regenError("That face is not cylindrical.", ["seed"]);
            cSys = surf.coordSystem;
            rHole = surf.radius;

            // Axial extent of the bore, measured in the cylinder's own frame.
            const b = evBox3d(context, { "topology" : faceQ, "cSys" : cSys, "tight" : true });
            faceLo = b.minCorner[2];
            faceHi = b.maxCorner[2];
        }
        else
        {
            const crv = evCurveDefinition(context, { "edge" : edgeQ });
            if (!(crv is Circle))
                throw regenError("That edge is not a circle.", ["seed"]);
            cSys = crv.coordSystem;
            rHole = crv.radius;
        }

        const body = qOwnerBody(seed);

        // ---- 2. Resolve the threaded span [zMin, zMax] in the cylinder frame.
        var zMin = 0 * meter;
        var zMax = 0 * meter;

        if (definition.depthMode == ThreadDepthMode.FULL_FACE)
        {
            if (!pickedFace)
                throw regenError("'Full selected face' needs a cylindrical FACE. Pick the hole wall, or switch to 'Specified depth'.", ["depthMode"]);
            zMin = faceLo;
            zMax = faceHi;
        }
        else
        {
            const d = definition.depth;
            if (pickedFace)
            {
                // Start at one end of the bore and run `d` into it.
                if (definition.flip)
                {
                    zMin = faceHi - d;
                    zMax = faceHi;
                }
                else
                {
                    zMin = faceLo;
                    zMax = faceLo + d;
                }
            }
            else
            {
                // Circle edge sits at z = 0 in its own frame; +z or -z is
                // arbitrary, so aim at whichever side actually has material.
                const bb = evBox3d(context, { "topology" : body, "cSys" : cSys, "tight" : false });
                var s = -1;
                if (abs(bb.maxCorner[2]) >= abs(bb.minCorner[2]))
                    s = 1;
                if (definition.flip)
                    s = -s;
                if (s > 0)
                {
                    zMin = 0 * meter;
                    zMax = d;
                }
                else
                {
                    zMin = -d;
                    zMax = 0 * meter;
                }
            }
        }

        if (zMax - zMin < TOLERANCE.zeroLength * meter)
            throw regenError("Threaded length is zero.", ["depth"]);

        // ---- 3. Thread profile. Depth is measured OUTWARD from the hole wall.
        const P = definition.pitch;
        const starts = definition.starts;
        const lead = P * starts;             // axial advance per full turn

        var h = 0 * meter;                   // radial groove depth
        var w = 0 * meter;                   // axial groove width at the wall
        if (definition.isoProfile)
        {
            h = 0.5413 * P;                  // (D - D1)/2, ISO 68-1 metric
            w = 1.1547 * h;                  // 2 * h * tan(30 deg)
        }
        else
        {
            h = definition.profileDepth;
            w = definition.profileWidth;
        }
        h = h + definition.clearance;
        w = w + 1.1547 * definition.clearance;

        // Push the tool past the wall so it never shares a coincident face
        // with the bore. Preserve the flank slope while overshooting, so the
        // groove is still exactly `h` deep as measured from the wall.
        const slope = (w / 2) / h;           // dimensionless: half-width per unit depth
        const eps = 0.1 * h;
        const rIn = rHole - eps;             // inside the bore void
        const rTip = rHole + h;              // deepest point of the cut
        const halfIn = slope * (h + eps);    // half-width of the tool at rIn

        // ---- 4. One helix + one swept groove per start.
        var handSign = 1;
        if (definition.rightHanded)
            handSign = -1;
        const zLo = zMin - lead;             // overshoot so the sweep clears
        const zHi = zMax + lead;             // the bore's end faces
        const spanM = (zHi - zLo) / meter;
        const leadM = lead / meter;
        const rM = rHole / meter;
        const totalTurns = spanM / leadM;

        const nPts = floor(totalTurns * 48) + 1;
        // toWorld(cSys), NOT transform(cSys). The latter passes the CoordSystem
        // straight through (keys stay origin/xAxis/zAxis), so it satisfies
        // `is Transform` yet has no `linear` -- every later multiply then dies
        // with "Can not multiply undefined and ValueWithUnits", pointing at the
        // vector rather than at the transform that is actually broken.
        const xf = toWorld(cSys);            // cylinder frame -> world

        var tools = [];
        var curves = [];

        for (var k = 0; k < starts; k += 1)
        {
            const tag = toString(k);
            const theta0 = 2 * PI * k / starts;   // phase offset of this start

            // 4a. Sample the helix, fit a spline through it.
            var pts = [];
            for (var i = 0; i < nPts; i += 1)
            {
                const t = i / (nPts - 1);
                const zM = zLo / meter + t * spanM;
                const theta = theta0 + handSign * 2 * PI * totalTurns * t;
                pts = append(pts, xf * (vector(rM * cos(theta * radian),
                                               rM * sin(theta * radian),
                                               zM) * meter));
            }
            opFitSpline(context, id + ("helix" ~ tag), { "points" : pts });
            curves = append(curves, qCreatedBy(id + ("helix" ~ tag), EntityType.BODY));

            // 4b. Profile plane at this start's helix origin.
            // Local x = radial outward at theta0; local y = +z (axial).
            const u = vector(cos(theta0 * radian), sin(theta0 * radian), 0);   // radial
            const n = vector(sin(theta0 * radian), -cos(theta0 * radian), 0);  // -tangent
            var sketch = newSketchOnPlane(context, id + ("profile" ~ tag), {
                "sketchPlane" : xf * plane(vector(0 * meter, 0 * meter, zLo), n, u)
            });

            // V pointing OUTWARD: tip in the material, base open to the bore.
            skLineSegment(sketch, "f1", { "start" : vector(rIn, -halfIn),      "end" : vector(rTip, 0 * meter) });
            skLineSegment(sketch, "f2", { "start" : vector(rTip, 0 * meter),   "end" : vector(rIn,  halfIn) });
            skLineSegment(sketch, "f3", { "start" : vector(rIn,  halfIn),      "end" : vector(rIn, -halfIn) });
            skSolve(sketch);

            // 4c. Sweep the groove.
            opSweep(context, id + ("sweep" ~ tag), {
                "profiles" : qSketchRegion(id + ("profile" ~ tag)),
                "path"     : qCreatedBy(id + ("helix" ~ tag), EntityType.EDGE)
            });
            tools = append(tools, qCreatedBy(id + ("sweep" ~ tag), EntityType.BODY));
        }

        // ---- 5. Subtract every groove from the part in one boolean.
        opBoolean(context, id + "cut", {
            "tools"         : qUnion(tools),
            "targets"       : body,
            "operationType" : BooleanOperationType.SUBTRACTION
        });

        // ---- 6. Drop the helix wire bodies (opSweep consumed them as paths,
        //         but the spline bodies themselves survive and clutter the tree).
        opDeleteBodies(context, id + "cleanup", { "entities" : qUnion(curves) });
    });

// ----------------------------------------------------------------------------
// USAGE (write_featurescript_feature, featureType: "internalThread"):
//
// M10x1.5 single-start RH tapped hole, full depth of the picked bore wall.
// Drill the hole at the MINOR diameter first: D1 = 10 - 1.0825*1.5 = 8.38 mm.
//   parameters: [
//     {id: "seed",        type: "query",    value: <the ø8.38 bore face>},
//     {id: "pitch",       type: "quantity", value: "1.5 mm"},
//     {id: "starts",      type: "quantity", value: 1},
//     {id: "depthMode",   type: "enum",     value: "FULL_FACE"},
//     {id: "rightHanded", type: "boolean",  value: true},
//     {id: "isoProfile",  type: "boolean",  value: true},
//     {id: "clearance",   type: "quantity", value: "0 mm"},
//   ]
//
// 2-start bottle-cap style thread, 20 mm deep from the picked rim circle,
// printed on FDM (loosened 0.3 mm so the mating part actually spins on):
//     {id: "seed",        <inner circular edge of the bore mouth>},
//     {id: "pitch",       "3 mm"},   // lead becomes 6 mm/turn
//     {id: "starts",      2},
//     {id: "depthMode",   "SPECIFIED"},
//     {id: "depth",       "20 mm"},
//     {id: "flip",        false},    // toggle if it threads the wrong way
//     {id: "clearance",   "0.3 mm"},
//
// Shallow, print-friendly custom profile (blunter than ISO, less stringing):
//     {id: "isoProfile",   false},
//     {id: "profileDepth", "0.6 mm"},
//     {id: "profileWidth", "1.2 mm"},
