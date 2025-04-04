#!/usr/bin/env python3

#
# Mapnik render script to produce LULC images (or other rasters) from 
# file-based geographic databases like GPKG.
#
# Created 2024-10-24 by Dirk 'jtk' Frommholz
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
    prog='renderLULC.py',
    description='Render specifically prepared GeoPackages into LULC bitmaps',
    epilog='example: renderLULC.py 13.3 52.5 13.4 52.6 1.0 Berlin.gpkg.zip Berlin_20cm.tif')
    
    # extent of scene to be rendered
    cmdLineParser.add_argument('lonMin', type=float,
    help='minimum longitude of scene extent')
    cmdLineParser.add_argument('latMin', type=float,
    help='minimum latitude of scene extent')
    cmdLineParser.add_argument('lonMax', type=float,
    help='maximum longitude of scene extent')
    cmdLineParser.add_argument('latMax', type=float,
    help='maximum latitude of scene extent')

    # extent of scene to be rendered
    cmdLineParser.add_argument('gsd', type=float,
    help='target ground sampling distance (GSD) of output')

    # input/output files
    cmdLineParser.add_argument('gpkgFile',
    help='input GeoPackage file (may be zipped with a single GPKG in the archive)')
    cmdLineParser.add_argument('outImage', 
    help='name of output image, file type is derived from extension')

    # options
    cmdLineParser.add_argument('--mapnik-render', default=
    'mapnik-render', help='path to the mapnik-render executable')
    cmdLineParser.add_argument('--mapnik-plugins', 
    help='path to the Mapnik input plugins (containing the *.input files)')
    cmdLineParser.add_argument('-v', '--version', action='version',
    version='%(prog)s 1.0')
    cmdLineParser.add_argument('--mapnik-style-sheet', default=
    'lulc_corine.xml', help='path to the Mapnik style sheet to be used')
    cmdLineParser.add_argument('--no-templates', action='store_true', 
    help='disable default XML template processing (for custom style sheets)')

    return cmdLineParser.parse_args()    


##############################################################################


def checkToolchain(args):
    """
    Checks if GDAL, cs2cs, mapnik-render and other tools are working properly.

    Args:
        args: the parsed command line arguments        
    """

    print('Checking toolchain')

    #
    # mapnik-render >= 4.0
    #
    mrMinVersion='4.0'
    mrResult=runExecutable([args.mapnik_render, '--version'])
    mrVersion=mrResult.output.split()        
  
    if len(mrVersion)==2 and mrVersion[0]=='version' and \
    parse_version(mrVersion[1])>=parse_version(mrMinVersion):
        print(args.mapnik_render,'is version', mrVersion[1])
    else:
        print('Cannot verify that', args.mapnik_render, 
        'is working properly or matches the minimum required version')
        sys.exit(1)

    # check plugins path if given
    # check common places if not (Linux only)
    mrOgrInput='ogr.input'        
    if args.mapnik_plugins:
        if os.path.exists(os.path.join(args.mapnik_plugins, mrOgrInput)) and \
        os.access(os.path.join(args.mapnik_plugins, mrOgrInput), os.X_OK):
            print('Manually specified Mapnik plugins under', 
            args.mapnik_plugins, 'look usable')
        else: 
            print('Cannot find and/or access Mapnik plugins under', 
            args.mapnik_plugins)
            sys.exit(1)
    else:

        for pluginPathCandidate in ['/usr/lib64/mapnik/input', 
        '/usr/lib/mapnik/input', '/lib64/mapnik/input', '/lib/mapnik/input']:
            if os.path.exists(os.path.join(pluginPathCandidate, mrOgrInput)) and \
            os.access(os.path.join(pluginPathCandidate, mrOgrInput), os.X_OK):
                args.mapnik_plugins=pluginPathCandidate                
                break
        
        if args.mapnik_plugins:
            print('Using Mapnik plugins from auto-deteced location', 
            args.mapnik_plugins)
        else:
            print('Cannot autodetect Mapnik plugins, please specify '
            'path on command line on I/O plugin errors')
            # sys.exit(1)

    #
    # GDAL tools
    #
    gdalMinVersion='3.9'

    for gdalTool in ['gdal_translate', 'ogr2ogr']:

        toolResult=runExecutable([gdalTool, '--version'])
        toolVersion=toolResult.output.replace(",", "").split()        

        if len(toolVersion)>=2 and toolVersion[0]=='GDAL' and \
        parse_version(toolVersion[1])>=parse_version(gdalMinVersion):
            print(gdalTool,'is version', toolVersion[1])
        else:
            print('Cannot verify that', gdalTool, 'is working properly or '\
            'matches the minimum required version')
            sys.exit(1)

    #
    # PROJ cs2cs
    #
    projTool='cs2cs'
    toolResult=runExecutable([projTool, '-f',  '%.0f', 'EPSG:4326', 
    'EPSG:3857'], '13 52\n')
    # keep floating-point numbers only
    toolOutputList=re.findall(r"[-+]?(?:\d*\.*\d+)", toolResult.output)
    # remove empty list entries
    toolOutputList=[x for x in toolOutputList if x]

    if len(toolOutputList)==3 and toolOutputList[0]=='5788614' and\
    toolOutputList[1]=='1459732' and toolOutputList[2]=='0':
        print(projTool, 'is capable of EPSG-based coordinate transforms')
    else:
        print(projTool, 'cannot perform EPSG-based coordinate transforms.',
        'Please check your PROJ setup, and set the PROJ_DATA environment',
        'variable to the PROJ EPSG database (see "Using proj" on',
        'https://proj.org).')
        sys.exit(1)

    print('Toolchain checks complete')


##############################################################################


def computeOutputDimensions(args):
    """
    Computes the extent in the target CRS, width and height of the output 
    LULC image, and the scale factor for linear features.

    Args:
        args: the parsed command line arguments        

    Returns:
        A list containing the output image width, output image height, and
        (unscaled) Mapnik GSD (=inverse GSD, in pixels per meter), and the
        extent in the target CRS as minX, minY maxX and maxY coordinates.
    """

    # source CRS is geodetic WGS84 
    # (attention: lat/lon, not lon/lat - this must be considered for cs2cs !)
    srcCrsStr='EPSG:4326'
    
    # target CRS is currently fixed to Web Mercator
    targetCrsName='WebMercator'
    targetCrsStr='EPSG:3857'

    # check longitudes and latitudes to be within the Web Mercator range
    if targetCrsName=='WebMercator':
        if args.lonMin<-180 or args.lonMin>180:
            print('Minimum longitude is out of range, must be -180 ... 180.')
            sys.exit(2)

        if args.lonMax<-180 or args.lonMax>180:
            print('Maximum longitude is out of range, must be -180 ... 180.')
            sys.exit(2)

        if args.latMin<-85 or args.latMin>85:
            print('Minimum latitude is out of range, must be -85 ... 85.')
            sys.exit(2)
 
        if args.latMax<-85 or args.latMax>85:
            print('Maximum latitude is out of range, must be -85 ... 85.')
            sys.exit(2)

    # GSD must be positive
    if args.gsd<=0:
        print('Target ground sampling distance must be positive')
        sys.exit(3)

    #                
    # transform minimum of extent into target CRS
    #

    toolResult=runExecutable(['cs2cs', '-f',  '%.10f', srcCrsStr, 
    targetCrsStr], str(args.latMin)+' '+str(args.lonMin)+'\n')
    # keep floating-point numbers only
    toolOutputList=re.findall(r"[-+]?(?:\d*\.*\d+)", toolResult.output)
    # remove empty list entries
    toolOutputList=[x for x in toolOutputList if x]

    if len(toolOutputList)!=3:
            print('Error while transforming minimum of extent to',targetCrsName,
            'target CRS')
            sys.exit(3)

    targetMinX=float(toolOutputList[0])
    targetMinY=float(toolOutputList[1])

    #                
    # transform maximum of extent into target CRS
    #

    toolResult=runExecutable(['cs2cs', '-f',  '%.10f', srcCrsStr, 
    targetCrsStr], str(args.latMax)+' '+str(args.lonMax)+'\n')
    # keep floating-point numbers only
    toolOutputList=re.findall(r"[-+]?(?:\d*\.*\d+)", toolResult.output)
    # remove empty list entries
    toolOutputList=[x for x in toolOutputList if x]

    if len(toolOutputList)!=3:
            print('Error while transforming maximum of extent to',targetCrsName,
            'target CRS')
            sys.exit(3)

    targetMaxX=float(toolOutputList[0])
    targetMaxY=float(toolOutputList[1])

    # compute metric extent dimensions in target CRS
    hDist=targetMaxX-targetMinX
    vDist=targetMaxY-targetMinY

    if hDist<0 or vDist<0:
        print('Horizontal or vertical extent dimensions negative, please '
        're-check order of coordinates and their signs')
        sys.exit(3)

    #
    # compute image dimensions from metric extent and GSD
    #
    imageWidth=round(hDist/args.gsd)
    imageHeight=round(vDist/args.gsd)

    # also compute the true GSD in the output image which may deviate from
    # the requested GSD due to roundoff errors
    effectiveHorizGSD=hDist/imageWidth;
    effectiveVertGSD=vDist/imageHeight;

    #
    # compute scale factor for linear features
    # from the medium latitude
    #

    mediumLatitude=args.latMin+0.5*(args.latMax-args.latMin)

    # scale factor for linear features
    # currently available for WebMercator only, but any (metric) conformal
    # projection will do
    scaleFactor=1.0
    if targetCrsName=='WebMercator':
        scaleFactor=1/math.cos(mediumLatitude*math.pi/180)
    else:
        print('Scale factor for linear features will not be used for', 
        targetCrsName, 'target CRS')

    # Mapnik GSD, i.e., GSD in pixels per meter
    mapnikGsdUnscaled=1.0/args.gsd
    # scaled Mapnik GSD in pixels per meter, becomes larger at poles
    mapnikGsd=mapnikGsdUnscaled*scaleFactor

    # print results 
    print('')
    print('Target CRS is', targetCrsName)
    print('Horizontal extent from', args.lonMin, 'deg to', args.lonMax, 
    'deg is', hDist, 'm in target CRS')
    print('Vertical extent from', args.latMin, 'deg to', args.latMax, 
    'deg is', vDist, 'm in target CRS')
    print('Output image dimensions at requested target GSD of', args.gsd, 
    'm/pixel will be', imageWidth, 'x', imageHeight, 'pixels')
    print('Output GSD due to integer rounding will change from', 
    str(args.gsd)+';'+str(args.gsd), '->', str(effectiveHorizGSD)+';'+
    str(effectiveVertGSD), 'm/pixel')
    print('Output GSD error therefore is', str(abs(args.gsd-
    effectiveHorizGSD)), 'horizontally and', str(abs(args.gsd-
    effectiveVertGSD)), 'm/pixels vertically')
    print('Scale factor for linear features at medium latitude of', 
    mediumLatitude, 'deg is', scaleFactor)
    print('Mapnik GSD will scale from', mapnikGsdUnscaled, '->', mapnikGsd, 
    'pixels/m')
    print('')

    # issue warning on huge image dimensions not correctly handled by Mapnik
    if imageWidth>=32768 or imageHeight>=32768:
        print('************************************************************')
        print('Warning: Output image width or height exceeds 32768 pixels.')
        print('Result may display errors. This seems to be a Mapnik issue.')
        print('************************************************************')

    # return output dimensions, GSD, and bounding box
    return [imageWidth, imageHeight, mapnikGsdUnscaled, targetMinX, 
    targetMinY, targetMaxX, targetMaxY]


##############################################################################


def modifyXmlTemplates(args, mapnikGsd):
    """
    Modifies XML template files to produce the final include files that
    configure the Mapnik style sheet using the XML entity mechanism.

    Args:
        args: the parsed command line arguments        
        mapnikGsd: the (unscaled) Mapnik GSD in pixels per meter
    """

    # extract style sheet path; this is where the include files should 
    # also be located
    ssPath=os.path.dirname(args.mapnik_style_sheet)


    #
    # change "entities.xml.inc" file from template to output a map in 
    # the target CRS and also set the Mapnik GSD (pixels per meter)
    #

    # WebMercator CTRS identifier
    # to be changed when other CRS are supported
    targetCrsStr='epsg:3857'

    # read template
    with open(os.path.join(ssPath, "entities.xml.inc.template"), "r") as source:
        lines=source.readlines()

    # alter and write XML
    with open(os.path.join(ssPath, "entities.xml.inc"), "w") as target:
        for line in lines:

            # alter CRS
            line=re.sub('<!ENTITY.*mapSrsID.*', '<!ENTITY mapSrsID "'+
            targetCrsStr+'">', line)

            # set Mapnik GSD
            line=re.sub('<!ENTITY.*GSD.*', '<!ENTITY GSD "'+
            f'{mapnikGsd:.12f}'+'">', line)

            # write
            target.write(line)

    #
    # change "datasource.xml.inc" file from template to use the 
    # same procedure
    #

    with open(os.path.join(ssPath, "datasource.xml.inc.template"), "r") as source:
        lines=source.readlines()

    # escape backslashes in GPKG path for regex replacement
    fixedGpkgFile=os.path.abspath(args.gpkgFile).replace('\\', r'\\')

    with open(os.path.join(ssPath, "datasource.xml.inc"), "w") as target:
        for line in lines:
            # set data source            
            line=re.sub('<Parameter name="file.*', '<Parameter name="file">'+
            fixedGpkgFile+'</Parameter>', line)

            # write
            target.write(line)



##############################################################################


def renderLULC(args, mapWidth, mapHeight, targetMinX, targetMinY, targetMaxX, 
targetMaxY):

    """
    Renders a Mapnik XML style sheet into a geo-referenced image of the given
    dimensions that covers the passed extent in target CRS coordinates. The
    file format and other output settings are taken from the command-line
    arguments in the args variable.

    Args:
        args: the parsed command line arguments        
        mapWidth: the width of the output image in pixels
        mapHeight: the height of the output image in pixels
        targetMinX: the minimum horizontal coordinate of the extent to be
        rendered, expressed in the target CRS
        targetMinY: the minimum vertical coordinate of the extent to be
        rendered, expressed in the target CRS
        targetMaxX: the maximum horizontal coordinate of the extent to be
        rendered, expressed in the target CRS
        targetMaxY: the maximum vertical coordinate of the extent to be
        rendered, expressed in the target CRS
    """

    # tempdir is output directory
    splitPath=os.path.split(os.path.abspath(args.outImage))
    pngImage=args.outImage+'.png'

    #
    # run Mapnik
    #
    mapnikCmdline=[args.mapnik_render, '--verbose', '--variables', 
    '--map-width', str(mapWidth), '--map-height', str(mapHeight), '--bbox',
    str(targetMinX)+','+str(targetMinY)+','+str(targetMaxX)+','+
    str(targetMaxY), '--img', pngImage, '--xml', args.mapnik_style_sheet]

    if args.mapnik_plugins:
        mapnikCmdline.append('--plugins-dir')
        mapnikCmdline.append(args.mapnik_plugins)

    print('Rendering started')
    renderStartTime=time.time()

    mapnikResult=runExecutable(mapnikCmdline, printCmdLine=True)
    print(mapnikResult.output)
    renderEndTime=time.time()

    if mapnikResult.exitCode!=0:
        print(args.mapnik_render, 'exited abnormally with code', 
        mapnikResult.exitCode)
        sys.exit(4)
    else: 
        print(args.mapnik_render, 'exited normally after', 
        int(renderEndTime-renderStartTime), 'seconds')

    #
    # add georefs
    #    
    print('')
    print('Converting', pngImage, 'to target', args.outImage)

    gdalResult=runExecutable(['gdal_translate', '-a_ullr', str(targetMinX), 
    str(targetMaxY), str(targetMaxX), str(targetMinY), '-a_srs', 'EPSG:3857', 
    pngImage, args.outImage], printCmdLine=True)
    print(gdalResult.output)

    if gdalResult.exitCode!=0:
        print('gdal_translate exited abnormally with code', 
        gdalResult.exitCode)
        sys.exit(4)
    else: 
        print('gdal_translate exited normally')

    # remove PNG
    os.remove(pngImage)


##############################################################################


def main(args):
    """
    The entry point controls the render workflow on a high level.

    Args:
        args: the parsed command line arguments        
    """

    # check for working toolchain
    checkToolchain(args)

    # compute output image dimensions
    [imageWidth, imageHeight, mapnikGsd, targetMinX, targetMinY, targetMaxX, 
    targetMaxY]=computeOutputDimensions(args)

    # modify XML entities in the default templates
    if not args.no_templates:
        modifyXmlTemplates(args, mapnikGsd)

    # render!
    renderLULC(args, imageWidth, imageHeight, targetMinX, targetMinY, 
    targetMaxX, targetMaxY)


##############################################################################


if __name__ == "__main__":
    main(parseCmdLine())
