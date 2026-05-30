FeatureScript 347;
import(path : "onshape/std/geometry.fs", version : "347.0");

/**
 * Copyright (c) Parametric Products Intellectual Holdings, LLC - All Rights Reserved
 *
 * 
 * NOTICE:  All information contained herein is, and remains
 * the property of Parametric Products Intellectual Holdings, LLC ("PPIH") and its suppliers,
 * if any.  The intellectual and technical concepts contained
 * herein are proprietary to Parametric Products Intellectual Holdings, LLC
 * and its suppliers and may be covered by U.S. and Foreign Patents,
 * patents in process, and are protected by trade secret or copyright law.
 * Dissemination of this information or reproduction of this material
 * is strictly forbidden unless prior written permission is obtained
 * from Parametric Products Intellectual Holdings, LLC.
 *  
 */

//debug
import(path : "d9e449e07522374d3a449ca1/e97335d4bdf059d05c1bbafa/0dc001b527bdec2e78360668", version : "a47bf2c7f721ca8261183c59");

//pointtoolslib.fs
import(path : "9e031a7ee1f671900cce3457/36ad36023cda8d6fb174f32c/091b30364a4e7d92ca08d39b", version : "b0dd237aa94ec6cf377a1da2");






export enum EdgeConnectionType{
    annotation { "Name" : "Spline" } SPLINE ,
    annotation { "Name" : "PolyLine" } POLYLINE ,
    annotation { "Name" : "Vertices" } VERTICES //,
    //annotation { "Name" : "BendRadius" } BEND_RADIUS
} 

export enum PointSurfaceType{
    annotation { "Name" : "Plane" } PLANE,
    annotation { "Name" : "Spline"} SPLINE
}

/**
 * 
 * Creates a surface from a set of 3d points.
 * 
 * The general approach is:
 *    1. Load a set of 3d points from a file
 *    2. sort the points into a rectangular grid
 *    3. connect all of the points in the x direction with splines opFitSpline
 *    4. loft between the splines in the y direction ( opLoft )
 * 
 * Example is here:
 *    https://forum.onshape.com/discussion/2254/filling-functionality-gaps-with-featurescript-rib-feature-3d-spline-fasteners-and-more#latest
 * 
 * In that example, the data is required to have a fixed pattern in x-y. 
 * For this, we do not need that, though it will work better if we have it, for sure
 * 
 * One big problem will be: what do we do when we have only one point for a given 'set' 
 *   of lines? 
 * and how do we find out how narrow to make the 'bands' that we use to make the ruled surface?
 * 
 * basically, we need to figure out a clever algo to organize a point cloud into a ruled surface.
 * 
 **/
/**
 * Creates a spline from 3-d points
 **/
annotation { "Feature Type Name" : "Edge From Points" }
export const edgeFromPoints = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "Point Data" }
        definition.pointData is string;
        
        
        annotation { "Name": "Connection Type" , "default": EdgeConnectionType.SPLINE}
        definition.connectionType is EdgeConnectionType;
              
       // if ( definition.connectionType == EdgeConnectionType.BEND_RADIUS ){
       //     annotation { "Name" : "Bend Radius"}
       //     isLength(definition.bendRadius, LENGTH_BOUNDS);               
       // }
    }
    {
        var pointArray = parseVertices(definition.pointData);
        
        //_debug(context,"SplineFromPoints", "found " ~ pointArray ~ " points.");
        
        if ( definition.connectionType == EdgeConnectionType.SPLINE ){
            createSplineFromPoints(context, id, pointArray);    
        }
        else if ( definition.connectionType == EdgeConnectionType.POLYLINE ){
            createPolyLineFromPoints(context, id, pointArray );
        }
        else if ( definition.connectionType == EdgeConnectionType.VERTICES ){
            createVerticesFromPoints(context, id, pointArray );   
        }
        //else {
        //    createBendRadiusPolyLineFromPoints(context, id, pointArray, definition.bendRadius );
        // }
        
}, {  } );


