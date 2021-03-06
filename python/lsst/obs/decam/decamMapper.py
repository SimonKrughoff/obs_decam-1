#
# LSST Data Management System
# Copyright 2012 LSST Corporation.
#
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
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
# You should have received a copy of the LSST License Statement and
# the GNU General Public License along with this program.  If not,
# see <http://www.lsstcorp.org/LegalNotices/>.
#
import re
import numpy as np
import lsst.afw.image as afwImage
import lsst.afw.image.utils as afwImageUtils
from lsst.daf.butlerUtils import CameraMapper
import lsst.pex.policy as pexPolicy

np.seterr(divide="ignore")

class DecamMapper(CameraMapper):
    packageName = 'obs_decam'

    def __init__(self, inputPolicy=None, **kwargs):
        policyFile = pexPolicy.DefaultPolicyFile(self.packageName, "DecamMapper.paf", "policy")
        policy = pexPolicy.Policy(policyFile)

        super(DecamMapper, self).__init__(policy, policyFile.getRepositoryPath(), **kwargs)

        afwImageUtils.defineFilter('u', lambdaEff=350, alias=['u DECam c0006 3500.0 1000.0'])
        afwImageUtils.defineFilter('g', lambdaEff=450, alias=['g DECam SDSS c0001 4720.0 1520.0'])
        afwImageUtils.defineFilter('r', lambdaEff=600, alias=['r DECam SDSS c0002 6415.0 1480.0'])
        afwImageUtils.defineFilter('i', lambdaEff=750, alias=['i DECam SDSS c0003 7835.0 1470.0'])
        afwImageUtils.defineFilter('z', lambdaEff=900, alias=['z DECam SDSS c0004 9260.0 1520.0'])
        afwImageUtils.defineFilter('y', lambdaEff=1000, alias=['Y DECam c0005 10095.0 1130.0'])

    def _extractDetectorName(self, dataId):
        nameTuple = self.registry.executeQuery(['side','ccd'], ['raw',], [('ccdnum','?'), ('visit','?')],
                                               None, (dataId['ccdnum'], dataId['visit']))
        if len(nameTuple) > 1:
            raise RuntimeError("More than one name returned")
        if len(nameTuple) == 0:
            raise RuntimeError("No name found for dataId: %s"%(dataId))
        return "%s%i" % (nameTuple[0][0], nameTuple[0][1])

    def _defectLookup(self, dataId, ccdSerial):
        """Find the defects for a given CCD.
        @param dataId (dict) Dataset identifier
        @param ccdSerial (string) CCD serial number
        @return (string) path to the defects file or None if not available
        """
        return None 

    def bypass_ccdExposureId(self, datasetType, pythonType, location, dataId):
        return self._computeCcdExposureId(dataId)
    def bypass_ccdExposureId_bits(self, datasetType, pythonType, location, dataId):
        return 32 # not really, but this leaves plenty of space for sources
    def _computeCcdExposureId(self, dataId):
        """Compute the 64-bit (long) identifier for a CCD exposure.

        @param dataId (dict) Data identifier with visit, ccd
        """
        visit = dataId['visit']
        ccdnum = dataId['ccdnum']
        return "%07d%02d" % (visit, ccdnum)

    def translate_dqmask(self, dqmask):
        # TODO: make a class member variable that knows the mappings
        # below instead of hard-coding them
        dqmArr    = dqmask.getArray()
        mask      = afwImage.MaskU(dqmask.getDimensions())
        mArr      = mask.getArray()
        idxBad    = np.where(dqmArr & 1)
        idxSat    = np.where(dqmArr & 2)
        idxIntrp  = np.where(dqmArr & 4)
        idxCr     = np.where(dqmArr & 16)
        idxBleed  = np.where(dqmArr & 64)
        idxEdge   = np.where(dqmArr & 512)
        mArr[idxBad]   |= mask.getPlaneBitMask("BAD")
        mArr[idxSat]   |= mask.getPlaneBitMask("SAT")
        mArr[idxIntrp] |= mask.getPlaneBitMask("INTRP")
        mArr[idxCr]    |= mask.getPlaneBitMask("CR")
        mArr[idxBleed] |= mask.getPlaneBitMask("SAT")
        mArr[idxEdge] |= mask.getPlaneBitMask("EDGE")
        return mask

    def translate_wtmap(self, wtmap):
        wtmArr = wtmap.getArray()
        idxUndefWeight = np.where(wtmArr <= 0)
        #Reassign weights to be finite but small:
        wtmArr[idxUndefWeight] = min(1e-14, np.min(wtmArr[np.where(wtmArr>0)]))
        var   = 1.0 / wtmArr
        varim = afwImage.ImageF(var)
        return varim
        
    def bypass_instcal(self, datasetType, pythonType, butlerLocation, dataId):
        # Workaround until I can access the butler
        instcalMap  = self.map_instcal(dataId)
        dqmaskMap   = self.map_dqmask(dataId)
        wtmapMap    = self.map_wtmap(dataId)
        instcalType = getattr(afwImage, instcalMap.getPythonType().split(".")[-1])
        dqmaskType  = getattr(afwImage, dqmaskMap.getPythonType().split(".")[-1])
        wtmapType   = getattr(afwImage, wtmapMap.getPythonType().split(".")[-1])
        instcal     = instcalType(instcalMap.getLocations()[0])
        dqmask      = dqmaskType(dqmaskMap.getLocations()[0])
        wtmap       = wtmapType(wtmapMap.getLocations()[0])

        mask        = self.translate_dqmask(dqmask)
        variance    = self.translate_wtmap(wtmap)

	mi          = afwImage.MaskedImageF(afwImage.ImageF(instcal.getImage()), mask, variance)
	md          = instcal.getMetadata()
	wcs         = afwImage.makeWcs(md)
	exp         = afwImage.ExposureF(mi, wcs)

        # Set the calib by hand; need to grab the zeroth extension
        header = re.sub(r'[\[](\d+)[\]]$', "[0]", instcalMap.getLocations()[0])
        md0 = afwImage.readMetadata(header)
        calib = afwImage.Calib()
        calib.setExptime(md0.get("EXPTIME"))
        calib.setFluxMag0(10**(0.4 * md0.get("MAGZERO")))
        exp.setCalib(calib)
        
        exp.setMetadata(md) # Do we need to remove WCS/calib info?
        return exp

    def _standardizeMasterCal(self, datasetType, item, dataId, setFilter=False):
        """Standardize a MasterCal image obtained from NOAO archive into Exposure

        These MasterCal images are MEF files with one HDU for each detector.
        Some WCS header, eg CTYPE1, exists only in the zeroth extensionr,
        so info in the zeroth header need to be copied over to metadata.

        @param datasetType: Dataset type ("bias" or "flat")
        @param item: The image read by the butler
        @param dataId: Data identifier
        @param setFilter: Whether to set the filter in the Exposure
        @return (lsst.afw.image.Exposure) the standardized Exposure
        """
        mi = afwImage.makeMaskedImage(item.getImage())
        md = item.getMetadata()
        masterCalMap = getattr(self, "map_" + datasetType)
        masterCalPath = masterCalMap(dataId).getLocations()[0]
        headerPath = re.sub(r'[\[](\d+)[\]]$', "[0]", masterCalPath)
        md0 = afwImage.readMetadata(headerPath)
        for kw in md0.paramNames():
            if kw not in md.paramNames():
                md.add(kw, md0.get(kw))
        wcs = afwImage.makeWcs(md, True)
        exp = afwImage.makeExposure(mi, wcs)
        exp.setMetadata(md)
        return self._standardizeExposure(self.calibrations[datasetType], exp, dataId, filter=setFilter)

    def std_bias(self, item, dataId):
        return self._standardizeMasterCal("bias", item, dataId, setFilter=False)

    def std_flat(self, item, dataId):
        return self._standardizeMasterCal("flat", item, dataId, setFilter=True)

    def std_fringe(self, item, dataId):
        exp = afwImage.makeExposure(afwImage.makeMaskedImage(item))
        return self._standardizeExposure(self.calibrations["fringe"], exp, dataId)
