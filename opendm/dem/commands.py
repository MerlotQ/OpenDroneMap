import os, glob
import gippy
import numpy
from scipy import ndimage
from datetime import datetime
from opendm import log
from loky import get_reusable_executor
from functools import partial

from . import pdal

def classify(lasFile, smrf=False, slope=1, cellsize=3, maxWindowSize=10, maxDistance=1,
             approximate=False, initialDistance=0.7, verbose=False):
    start = datetime.now()

    try:
        if smrf:
            pdal.run_pdaltranslate_smrf(lasFile, lasFile, slope, cellsize, maxWindowSize, verbose)
        else:
            pdal.run_pdalground(lasFile, lasFile, slope, cellsize, maxWindowSize, maxDistance, approximate=approximate, initialDistance=initialDistance, verbose=verbose)
    except:
        raise Exception("Error creating classified file %s" % fout)

    log.ODM_INFO('Created %s in %s' % (os.path.relpath(lasFile), datetime.now() - start))
    return lasFile


def create_dems(filenames, demtype, radius=['0.56'], gapfill=False,
                outdir='', suffix='', resolution=0.1, max_workers=None, **kwargs):
    """ Create DEMS for multiple radii, and optionally gapfill """
    fouts = []
    
    create_dem_for_radius = partial(create_dem, 
        filenames, demtype,
        outdir=outdir, suffix=suffix, resolution=resolution, **kwargs)
    
    with get_reusable_executor(max_workers=max_workers, timeout=None) as e:
         fouts = list(e.map(create_dem_for_radius, radius))
    
    fnames = {}
    # convert from list of dicts, to dict of lists
    for product in fouts[0].keys():
        fnames[product] = [f[product] for f in fouts]
    fouts = fnames

    # gapfill all products
    _fouts = {}
    if gapfill:
        for product in fouts.keys():
            # output filename
            fout = os.path.join(outdir, '%s%s.tif' % (demtype, suffix))
            gap_fill(fouts[product], fout)
            _fouts[product] = fout
    else:
        # only return single filename (first radius run)
        for product in fouts.keys():
            _fouts[product] = fouts[product][0]

    return _fouts


def create_dem(filenames, demtype, radius, decimation=None,
               maxsd=None, maxz=None, maxangle=None, returnnum=None,
               products=['idw'], outdir='', suffix='', verbose=False, resolution=0.1):
    """ Create DEM from collection of LAS files """
    start = datetime.now()
    # filename based on demtype, radius, and optional suffix
    bname = os.path.join(os.path.abspath(outdir), '%s_r%s%s' % (demtype, radius, suffix))
    ext = 'tif'

    fouts = {o: bname + '.%s.%s' % (o, ext) for o in products}
    prettyname = os.path.relpath(bname) + ' [%s]' % (' '.join(products))

    log.ODM_INFO('Creating %s from %s files' % (prettyname, len(filenames)))
    # JSON pipeline
    json = pdal.json_gdal_base(bname, products, radius, resolution)
    
    # A DSM for meshing does not use additional filters
    if demtype != 'mesh_dsm':
        json = pdal.json_add_filters(json, maxsd, maxz, maxangle, returnnum)
    
    if demtype == 'dsm':
        json = pdal.json_add_classification_filter(json, 2, equality='max')
    elif demtype == 'dtm':
        json = pdal.json_add_classification_filter(json, 2)

    if decimation is not None:
        json = pdal.json_add_decimation_filter(json, decimation)

    pdal.json_add_readers(json, filenames)

    pdal.run_pipeline(json, verbose=verbose)
    
    # verify existence of fout
    exists = True
    for f in fouts.values():
        if not os.path.exists(f):
            exists = False
    if not exists:
        raise Exception("Error creating dems: %s" % ' '.join(fouts))

    log.ODM_INFO('Completed %s in %s' % (prettyname, datetime.now() - start))
    return fouts


def gap_fill(filenames, fout):
    """ Gap fill from higher radius DTMs, then fill remainder with interpolation """
    start = datetime.now()

    if len(filenames) == 0:
        raise Exception('No filenames provided!')

    log.ODM_INFO('Starting gap-filling with nearest interpolation...')
    filenames = sorted(filenames)

    imgs = map(gippy.GeoImage, filenames)
    nodata = imgs[0][0].nodata()
    arr = imgs[0][0].read()

    for i in range(1, len(imgs)):
        locs = numpy.where(arr == nodata)
        arr[locs] = imgs[i][0].read()[locs]

    # Nearest neighbor interpolation at bad points
    indices = ndimage.distance_transform_edt(arr == nodata, 
                                    return_distances=False, 
                                    return_indices=True)
    arr = arr[tuple(indices)]

    # Median filter
    from scipy import signal
    arr = signal.medfilt(arr, 5)
    
    # write output
    imgout = gippy.GeoImage.create_from(imgs[0], fout)
    imgout.set_nodata(nodata)
    imgout[0].write(arr)
    fout = imgout.filename()
    imgout = None

    log.ODM_INFO('Completed gap-filling to create %s in %s' % (os.path.relpath(fout), datetime.now() - start))

    return fout