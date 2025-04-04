#!/usr/bin/env python3

#
# Converter script to turn OpenStreetMap (OSM) serializations (.osm, 
# .osm.pbf etc.) into (compressed) GeoPackage files (.gpkg) files and
# filter relevant entries
#
# Created 2024-10-30 by Dirk 'jtk' Frommholz
# DLR OS-SEC, Berlin-Adlershof, Germany
#
# Written in Python - not pretty, but functional.
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import subprocess
import os
import sys
import argparse
import re
from types import SimpleNamespace
import math
import time

# needs 'pip install packaging'
from packaging.version import parse as parse_version


##############################################################################


def runExecutable(args, stdinStr='', printCmdLine=False):
    """
    Runs program with parameters and returns the exit code and output as
    a dictionary.

    Args:
        args: the program name and arguments as a string list
        stdinStr: the string to be used as stdin input (you may have to add
                  a newline to terminate)

    Returns:
        A SimpleNamespace with the members "exitCode" and "output" for
        the exit code and merged stdout/err output text respectively encoded
        using the OS device codepage
    """

    # we need this to get the console output formatted correctly
    deviceEncoding=os.device_encoding(1)

    if printCmdLine:
        if stdinStr:
            print('echo', stdinStr, '|', ' '.join(args))
        else:
            print(' '.join(args))

    try:
        output=subprocess.check_output(args, stderr=subprocess.STDOUT, 
        input=stdinStr, shell=False, encoding=deviceEncoding)
    except subprocess.CalledProcessError as exc:
        return SimpleNamespace(exitCode=exc.returncode, output=exc.output)
    else:
        return SimpleNamespace(exitCode=0, output=output)


##############################################################################


def parseCmdLine():
    """
    Parses the command line arguments.

    Returns:
        The parsed arguments which can be accessed as member variables.
    """

    # parse command line arguments
    cmdLineParser=argparse.ArgumentParser(
    prog='osmToGpkg.py',
    description='Convert OpenStreetMap serializations into GeoPackages',
    epilog='example: osmToGpkg.py --osmconf osmconf_lulc.ini --waterlayer '
    'water.gpkg.zip --baselayer base.gpkg.zip Berlin.osm.pbf Berlin.gpkg.zip')

    # extent of scene to be rendered
    cmdLineParser.add_argument('osmSerialization', 
    help='OpenStreetMap serialization (.osm, .osm.pbf file)')
    cmdLineParser.add_argument('output', 
    help='output GeoPackage, with gpkg.zip extension to use compression')

    # options
    cmdLineParser.add_argument('--osmconf', default='osmconf_lulc.ini',
    help='path to the osmconf file controlling OSM feature extraction')
    cmdLineParser.add_argument('--ogropts', nargs='+', help=
    'additional options to be verbatimly passed to ogr2ogr, e.g. -clipsrc')
    cmdLineParser.add_argument('--waterlayer', 
    default='water_polygons_osmenc.gpkg.zip',
    help='path to the water polygons GeoPackage modelling large water bodies')
    cmdLineParser.add_argument('--baselayer', 
    default='ESA_WorldCover_10m_2021_v200_merged_0_0025deg_ip.gpkg.zip',
    help='path to the base layer filling areas not modelled by OpenStreetMap')
    cmdLineParser.add_argument('-v', '--version', action='version',
    version='%(prog)s 1.0')

    return cmdLineParser.parse_args()    


##############################################################################


def checkToolchain(args):
    """
    Checks if GDAL/OGR other tools are working properly.

    Args:
        args: the parsed command line arguments        
    """

    print('Checking toolchain')

    #
    # GDAL tools
    #
    gdalMinVersion='3.9'

    for gdalTool in ['ogr2ogr', 'ogrinfo']:

        toolResult=runExecutable([gdalTool, '--version'])
        toolVersion=toolResult.output.replace(",", "").split()

        if len(toolVersion)>=2 and toolVersion[0]=='GDAL' and \
        parse_version(toolVersion[1])>=parse_version(gdalMinVersion):
            print(gdalTool,'is version', toolVersion[1])
        else:
            print('Cannot verify that', gdalTool, 'is working properly or '\
            'matches the minimum required version')
            sys.exit(1)

    print('Toolchain checks complete')


##############################################################################


def querySingleExtent(ogrFile, layerName):
    """
    Computes the extent of the given feature layer of the input vector file,
    i.e., OSM serializations or GeoPackages, assuming geodetic coordinates.

    Args:
        args: the parsed command line arguments        

    Returns:
        The bounding box of the feature layer of the OSM serialization as 
        a floating-point lonMin, latMin, lonMax, latMax list.
    """

    toolResult=runExecutable(['ogrinfo', '-summary',  ogrFile, layerName])

    if toolResult.exitCode!=0:
        print('Extent extraction finished with errors (did you correctly '
        'set the GDAL_DATA and PROJ_DATA environment variables ?)')
        sys.exit(2)

    # search for 'Extent: ...' line in program output
    toolOutputList=re.findall(r"Extent:.*", toolResult.output)
    if len(toolOutputList)!=1:
        print('Failed to extract extent for layer', layerName, 
        'from OSM serialization')
        sys.exit(2)

    # keep floating-point numbers only
    toolOutputList=re.findall(r"[-+]?(?:\d*\.*\d+)", toolOutputList[0])
    # remove empty list entries (should not happen)
    toolOutputList=[x for x in toolOutputList if x]

    if len(toolOutputList)!=4:
        print('Found extent for layer', layerName, 'in OSM serialization,'
        ' but expected four not', len(toolOutputList), 'coordinates')
        sys.exit(2)

    return [float(toolOutputList[0]), float(toolOutputList[1]), 
    float(toolOutputList[2]), float(toolOutputList[3])]


##############################################################################


def computeExtent(ogrFile):
    """
    Computes the common extent of the multipolygons, lines and points layers 
    of the input vector file, i.e., OSM serializations or GeoPackages, 
    assuming geodetic coordinates.

    Args:
        args: the parsed command line arguments        

    Returns:
        The common bounding box of the above layers of the OSM serialization 
        as a floating-point lonMin, latMin, lonMax, latMax list.
    """
                    
    # query extents
    multiPolysExtent=querySingleExtent(ogrFile, 'multipolygons')
    linesExtent=querySingleExtent(ogrFile, 'lines')
    pointsExtent=querySingleExtent(ogrFile, 'points')

    # compute common bounding box
    lonMin=min(multiPolysExtent[0], linesExtent[0], pointsExtent[0]) 
    latMin=min(multiPolysExtent[1], linesExtent[1], pointsExtent[1]) 
    lonMax=max(multiPolysExtent[2], linesExtent[2], pointsExtent[2]) 
    latMax=max(multiPolysExtent[3], linesExtent[3], pointsExtent[3]) 

    return [lonMin, latMin, lonMax, latMax]


##############################################################################


def convertOsmScene(args):
    """
    Converts the OSM serialization into a GeoPackage and merges it with the
    base and water polygon  layers.

    Args:
        args: the parsed command line arguments        
    """

    tempOutput=os.path.splitext(args.output)[0]+'_temp.gpkg'

    #
    # convert serialization into raw GPKG
    #
    print('')
    print('Creating raw GPKG', tempOutput,'from OSM serialization',
    args.osmSerialization)

    # insert verbatim OGR options at the right place, if any
    toolCmdline=['ogr2ogr', '-f', 'GPKG', '--config', 
    'OSM_CONFIG_FILE='+args.osmconf, tempOutput, args.osmSerialization]

    if args.ogropts:
        toolCmdline.insert(5, args.ogropts)

    toolResult=runExecutable(toolCmdline, printCmdLine=True)
    print(toolResult.output)

    if toolResult.exitCode!=0:
        print('Conversion of OSM serialization into raw GPKG', 
        tempOutput, 'failed')
        sys.exit(3)

    #
    # compute extent from GPKG incorporating any initial OSM scene 
    # crop/selection
    #
    print('Computing extents of raw GPKG serialization', args.osmSerialization)
    [lonMin, latMin, lonMax, latMax]=computeExtent(tempOutput)
    print('... which is', [lonMin, latMin, lonMax, latMax])

    #
    # integrate base layer
    #
    print('Integrating base layer', args.baselayer, 'into', tempOutput)
    toolResult=runExecutable(['ogr2ogr', '-f', 'GPKG', '-update', '-nlt',
    'PROMOTE_TO_MULTI', '-nln', 'multipolygons_baselayer', '-wrapdateline',
    '-clipsrc', str(lonMin), str(latMin), str(lonMax), str(latMax), 
    tempOutput, args.baselayer, 'multipolygons'], printCmdLine=True)
    print(toolResult.output)

    if toolResult.exitCode!=0:
        print('Integration of base layer', args.baselayer, 'into', 
        tempOutput, 'failed')
        sys.exit(3)

    #
    # integrate water polygons
    #
    print('Integrating water polygons', args.waterlayer, 'into', tempOutput)
    toolResult=runExecutable(['ogr2ogr', '-f', 'GPKG', '-update', '-nlt',
    'PROMOTE_TO_MULTI', '-nln', 'multipolygons_water', '-wrapdateline',
    '-clipsrc', str(lonMin), str(latMin), str(lonMax), str(latMax), 
    tempOutput, args.waterlayer, 'multipolygons'], printCmdLine=True)
    print(toolResult.output)

    if toolResult.exitCode!=0:
        print('Integration of water layer', args.waterlayer, 'into', 
        tempOutput, 'failed')
        sys.exit(3)

    #
    # convert to final result
    #
    print('Finalizing output', args.output)
    toolResult=runExecutable(['ogr2ogr', args.output, tempOutput], 
    printCmdLine=True)
    print(toolResult.output)

    if toolResult.exitCode!=0:
        print('Finalization of output', args.output, 'from', tempOutput, 
        'failed')
        sys.exit(3)

    # clean up temporary output
    os.remove(tempOutput)


##############################################################################


def main(args):
    """
    The entry point controls the render workflow on a high level.

    Args:
        args: the parsed command line arguments        
    """

    # check for working toolchain
    checkToolchain(args)

    # convert
    convertOsmScene(args)


##############################################################################


if __name__ == "__main__":
    main(parseCmdLine())
