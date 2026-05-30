FeatureScript 336;
import(path : "onshape/std/geometry.fs", version : "336.0");

annotation { "Feature Type Name" : "3D Spline" }
export const spline3D = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "Vertices", "Filter" : EntityType.VERTEX }
        definition.vertices is Query;

        annotation { "Name" : "Closed" }
        definition.closed is boolean;
    }
    {
        // Part 1 of 2 calls for making the feature patternable via feature pattern.
        var remainingTransform = getRemainderPatternTransform(context, { "references" : definition.vertices });

        var points = [];
        for (var vertex in evaluateQuery(context, definition.vertices))
        {
            points = append(points, evVertexPoint(context, { "vertex" : vertex }));
        }
        if (definition.closed)
        {
            if (size(points) <= 2)
                throw regenError("A closed spline must have at least 3 points");
            points = append(points, points[0]);
        }
        opFitSpline(context, id, { "points" : points });
        
        // Part 2 of 2 calls for making the feature patternable via feature pattern.
        transformResultIfNecessary(context, id, remainingTransform);
    }, { closed : false });
