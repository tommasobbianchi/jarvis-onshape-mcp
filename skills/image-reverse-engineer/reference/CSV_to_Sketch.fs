FeatureScript 2455;
import(path : "onshape/std/common.fs", version : "2455.0");

ICON :: import(path : "52c097982b61a892bd512048", version : "0160248113e4c943a68de61b");
DESCRIPTION::import(path : "1858d6b9eeed29f3d0a1a43d", version : "d6f4b934332bc45ec5ca228f");

// Created by Caden Armstrong
// SmartBench Software

// For custom FeatureScript and Onshape integrated applications
// visit www.smartbenchsoftware.com
// The leading provider of Onshape Custom Solutions


export enum ConversionType
{
    annotation { "Name" : "Points" }
    POINT,
    annotation { "Name" : "Polyline" }
    POLYLINE,
    annotation { "Name" : "Spline" }
    SPLINE

}

export enum Units
{
    annotation { "Name" : "Millimeter" }
    MILLIMETER,
    annotation { "Name" : "Inch" }
    INCH,
    annotation { "Name" : "Meter" }
    METER,
    annotation { "Name" : "Feet" }
    FEET
}

annotation { "Feature Type Name" : "CSV to Sketch", "Icon" : ICON::BLOB_DATA, "Feature Type Description" : "Import a CSV file as sketch points, a polylines, or splines.",
"Description Image":DESCRIPTION::BLOB_DATA}
export const csvToSketch = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "CSV Data" }
        definition.csvData is TableData;

        annotation { "Name" : "Units" }
        definition.units is Units;

        annotation { "Name" : "Sketch Plane", "Filter" : GeometryType.PLANE, "MaxNumberOfPicks" : 1 }
        definition.plane is Query;

        annotation { "Name" : "Conversion Type" }
        definition.conversionType is ConversionType;




    }
    {
        // Define the function's action
        var sketch1 = newSketchOnPlane(context, id + "sketch1", {
                "sketchPlane" : evPlane(context, {
                        "face" : definition.plane
                    })
            });

        var unit = millimeter;
        if (definition.units == Units.INCH)
        {
            unit = inch;
        }
        else if (definition.units == Units.METER)
        {
            unit = meter;
        }
        else if (definition.units == Units.FEET)
        {
            unit = 12 * inch;
        }

        var csv = definition.csvData.csvData;
        println(csv);
        if (definition.conversionType == ConversionType.POINT)
        {
            for (var i = 0; i < size(csv); i += 1)
            {
                if (toString(csv[i][0]) == "" || size(csv[i]) < 2 || toString(csv[i][1]) == "" )
                {
                    continue;
                }
                skPoint(sketch1, ("point" ~ toString(i)), {
                            "position" : (csv[i] as Vector) * unit
                        });
            }

        }
        else if (definition.conversionType == ConversionType.POLYLINE)
        {
            var pointdata = [];
            var splinecount = 1;
            for (var i = 0; i < size(csv); i += 1)
            {

                if (toString(csv[i][0]) == "" || toString(csv[i][1]) == "" )
                {

                    skPolyline(sketch1, ("spline" ~ toString(splinecount)), {
                                "points" : pointdata
                            });
                    splinecount += 1;
                    pointdata = [];

                    continue;
                }
                pointdata = append(pointdata, (csv[i] as Vector) * unit);
            }

            skPolyline(sketch1, ("spline" ~ toString(splinecount)), {
                        "points" : pointdata
                    });

        }
        else if (definition.conversionType == ConversionType.SPLINE)
        {
            var pointdata = [];
            var splinecount = 1;
            for (var i = 0; i < size(csv); i += 1)
            {

                if (toString(csv[i][0]) == ""|| toString(csv[i][1]) == "" )
                {

                    skFitSpline(sketch1, ("spline" ~ toString(splinecount)), {
                                "points" : pointdata
                            });
                    splinecount += 1;
                    pointdata = [];

                    continue;
                }
          
                pointdata = append(pointdata, (csv[i] as Vector) * unit);
            }

            skFitSpline(sketch1, ("spline" ~ toString(splinecount)), {
                        "points" : pointdata
                    });


        }

        skSolve(sketch1);


    });

