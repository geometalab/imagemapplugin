import os

from PyQt4 import QtGui
from PyQt4.QtCore import *
from PyQt4.QtGui import *

from qgis.core import *

from html_image_map_creator_gui import HTMLImageMapCreatorGUI

# Initialize Qt resources from file
import html_image_map_creator_rc
import codecs
import json

# Constants
VALID_GEOMETRY_TYPES = {
    QGis.WKBPoint,
    QGis.WKBMultiPoint,
    QGis.WKBPolygon,
    QGis.WKBMultiPolygon
}
PLUGIN_PATH = os.path.dirname(__file__)
FULL_TEMPLATE_DIR = u'{}/templates/full'.format(PLUGIN_PATH)
LABEL_TEMPLATE_DIR = u'{}/templates/label'.format(PLUGIN_PATH)
INFO_TEMPLATE_DIR = u'{}/templates/info_box'.format(PLUGIN_PATH)
POINT_AREA_BUFFER = 10  # (plus-minus 2x <constant> 20pixel areas)


class HTMLImageMapCreatorPlugin:

    MSG_BOX_TITLE = "QGIS HTML Image Map Creator "

    def __init__(self, iface):
        # Save reference to the QGIS interface and initialize instance variables
        self.iface = iface
        self.files_path = ""
        self.layer_id = u''
        self.labels = []
        self.info_boxes = []
        self.area_index = 0
        self.label_currently_checked = False
        self.info_currently_checked = False

    def initGui(self):
        # Create action that will start plugin configuration
        self.action = QAction(QIcon(":/html_image_map_creator_icon.xpm"), "Create map...", self.iface.mainWindow())
        self.action.setWhatsThis("Configuration for Image Map Creator")
        QObject.connect(self.action, SIGNAL("triggered()"), self.run)
        # Add toolbar button and menu item
        self.iface.addToolBarIcon(self.action)
        if hasattr(self.iface, "addPluginToWebMenu"):
            self.iface.addPluginToWebMenu("&HTML Image Map Creator", self.action)
        else:
            self.iface.addPluginToMenu("&HTML Image Map Creator", self.action)
        # Connect to signal renderComplete which is emitted when canvas rendering is done
        QObject.connect(self.iface.mapCanvas(), SIGNAL("renderComplete(QPainter *)"), self.renderTest)

    def unload(self):
        # Remove the plugin menu item and icon
        if hasattr(self.iface, "addPluginToWebMenu"):
            self.iface.removePluginWebMenu("&HTML Image Map Creator", self.action)
        else:
            self.iface.removePluginMenu("&HTML Image Map Creator", self.action)
        self.iface.removeToolBarIcon(self.action)
        # Disconnect from canvas signal
        QObject.disconnect(self.iface.mapCanvas(), SIGNAL("renderComplete(QPainter *)"), self.renderTest)

    def run(self):
        # Check if active layer is a polygon layer:
        self.layer = self.iface.activeLayer()
        if not self.isLayerValid():
            return
        # We need the fields of the active layer to show in the attribute combobox in the gui:
        self.attr_fields = self.loadFields()
        if not self.attr_fields:
            QMessageBox.warning(self.iface.mainWindow(), self.MSG_BOX_TITLE, (
              "No fields in attribute table\n"
              "Please add at least one field in the attribute table for this layer\n"
              "and then restart the plugin."), QMessageBox.Ok, QMessageBox.Ok)
            return
        # Construct GUI (using these fields)
        flags = Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMaximizeButtonHint  # QgisGui.ModalDialogFlags
        self.htmlImageMapCreatorGui = HTMLImageMapCreatorGUI(self.iface.mainWindow(), flags)
        self.htmlImageMapCreatorGui.txtFileName.textChanged.connect(self.setCurrentFilesPath)
        self.htmlImageMapCreatorGui.setOkButtonState(False)
        self.htmlImageMapCreatorGui.setAttributeFields(self.attr_fields)
        self.layerAttr = self.attr_fields
        self.selectedFeaturesOnly = False  # default: all features in current extent
        # Catch SIGNALs
        signals = [
            ("getFilesPath(QString)", self.setFilesPath),
            ("getLayerName(QString)", self.setLayerName),
            ("getFeatureTotal(QString)", self.setFeatureTotal),
            ("getDimensions(QString)", self.setDimensions),
            ("labelAttributeSet(QString)", self.labelAttributeFieldSet),
            ("spinLabelSet(int)", self.setLabelOffset),
            ("getCbkBoxLabel(bool)", self.setLabelChecked),
            ("getCurrentLabelState(bool)", self.setCurrentLabelState),
            ("infoBoxAttributeSet(QString)", self.infoBoxAttributeFieldSet),
            ("spinInfoSet(int)", self.setInfoOffset),
            ("getCbkBoxInfo(bool)", self.setInfoChecked),
            ("getCurrentInfoState(bool)", self.setCurrentInfoState),
            ("getCbkBoxSelectedOnly(bool)", self.setSelectedOnly),
            ("getSelectedFeatureCount(QString)", self.setFeatureCount),
            ("go(QString)", self.go),
            ("setMapCanvasSize(int, int)", self.setMapCanvasSize),
        ]
        for code, slot in signals:
            QObject.connect(self.htmlImageMapCreatorGui, SIGNAL(code), slot)
        # Reload GUI states & check if it is 'ready'
        self.reloadGuiStates()
        # Set active layer name and expected image dimensions
        self.htmlImageMapCreatorGui.setLayerName(self.layer.name())
        self.htmlImageMapCreatorGui.setFeatureTotal("<b>{}</b> features total".format(self.layer.featureCount()))
        canvas_width = self.iface.mapCanvas().width()
        canvas_height = self.iface.mapCanvas().height()
        dimensions = "width ~<b>{}</b> pixels, height ~<b>{}</b> pixels"
        self.htmlImageMapCreatorGui.setDimensions(dimensions.format(canvas_width, canvas_height))
        # Set number of selected features
        selected_features = self.layer.selectedFeatureCount()
        selected_features_in_extent = self.nofSelectedFeaturesInExtent()
        if selected_features > 0 and selected_features_in_extent > 0:
            self.htmlImageMapCreatorGui.chkBoxSelectedOnly.setEnabled(True)
            self.htmlImageMapCreatorGui.featureCount.setEnabled(True)
        select_msg = "<b>{}</b> selected, of which <b>{}</b> from map view will be exported"
        self.htmlImageMapCreatorGui.setFeatureCount(select_msg.format(selected_features, selected_features_in_extent))
        self.htmlImageMapCreatorGui.show()

    # Enables Ok-button in GUI if conditions are met
    def isReady(self):
        if (self.current_filename and
           (self.label_currently_checked or self.info_currently_checked)):
            self.htmlImageMapCreatorGui.setOkButtonState(True)
        else:
            self.htmlImageMapCreatorGui.setOkButtonState(False)

    # Loads fields in attribute table to be listed in comboboxes
    def loadFields(self):
        fields = []
        pending_fields = self.layer.pendingFields()
        if hasattr(pending_fields, 'iteritems'):
            for (i, field) in pending_fields.iteritems():
                fields.append(field.name().trimmed())
        else:
            for field in pending_fields:
                fields.append(field.name().strip())
        return fields

    # Reloads states of GUI components from previous session
    def reloadGuiStates(self):
        self.htmlImageMapCreatorGui.setFilesPath(self.files_path)
        # Only reload states if the plugin is used on the same layer as before:
        if self.layer_id == self.layer.id():
            # Reload selected features in combo-boxes:
            if self.label_field_index < len(self.attr_fields):
                self.htmlImageMapCreatorGui.cmbLabelAttributes.setCurrentIndex(self.label_field_index)
            if self.info_field_index < len(self.attr_fields):
                self.htmlImageMapCreatorGui.cmbInfoBoxAttributes.setCurrentIndex(self.info_field_index)
            # Reload spin-box values:
            self.htmlImageMapCreatorGui.spinBoxLabel.setValue(self.label_offset)
            self.htmlImageMapCreatorGui.spinBoxInfo.setValue(self.info_offset)
            # Reload check-box states:
            label_state = Qt.Checked if self.label_checked else Qt.Unchecked
            info_state = Qt.Checked if self.info_checked else Qt.Unchecked
            self.htmlImageMapCreatorGui.chkBoxLabel.setCheckState(label_state)
            self.htmlImageMapCreatorGui.chkBoxInfoBox.setCheckState(info_state)
        else:
            # When opened on a different layer, reset checkbox conditions:
            self.label_currently_checked = False
            self.info_currently_checked = False
        self.isReady()

    def isLayerValid(self):
        if self.layer is None:
            QMessageBox.warning(self.iface.mainWindow(), self.MSG_BOX_TITLE, (
              "No active layer found\n"
              "Please select a (multi-)polygon or (multi-)point layer first, \n"
              "by selecting it in the legend."), QMessageBox.Ok, QMessageBox.Ok)
            return False
        # Don't know if this is possible / needed
        if not self.layer.isValid():
            QMessageBox.warning(self.iface.mainWindow(), self.MSG_BOX_TITLE, (
              "No VALID layer found\n"
              "Please select a valid (multi-)polygon or (multi-)point layer first, \n"
              "by selecting it in the legend."), QMessageBox.Ok, QMessageBox.Ok)
            return False
        if (self.layer.type() > 0):  # 0 = vector, 1 = raster
            QMessageBox.warning(self.iface.mainWindow(), self.MSG_BOX_TITLE, (
              "Wrong layer type, only vector layers may be used...\n"
              "Please select a vector layer first, \n"
              "by selecting it in the legend."), QMessageBox.Ok, QMessageBox.Ok)
            return False
        self.provider = self.layer.dataProvider()
        if self.provider.geometryType() not in VALID_GEOMETRY_TYPES:
            QMessageBox.warning(self.iface.mainWindow(), self.MSG_BOX_TITLE, (
              "Wrong geometry type, only (multi-)polygons and (multi-)points may be used.\n"
              "Please select a (multi-)polygon or (multi-)point layer first, \n"
              "by selecting it in the legend."), QMessageBox.Ok, QMessageBox.Ok)
            return False
        return True

    def writeHtml(self):
        # Create a holder for retrieving features from the provider
        feature = QgsFeature()
        temp = unicode(self.files_path+".png")
        imgfilename = os.path.basename(temp)
        html = [u'<!DOCTYPE HTML>\n<html>']
        isLabelChecked = self.htmlImageMapCreatorGui.isLabelChecked()
        isInfoChecked = self.htmlImageMapCreatorGui.isInfoBoxChecked()
        onlyLabel = isLabelChecked and not isInfoChecked
        onlyInfo = isInfoChecked and not isLabelChecked
        html.append(u'\n<head>\n<title>' + self.layer.name() +
                    '</title>\n<meta charset="UTF-8">')
        # Write necessary CSS content for corresponding features, namely "label" and "infoBox":
        html.append(u'\n<style type="text/css">\n')
        filename = "css.txt"
        # Empty list as offset-replacement-parameter, because there are no replacements to be made.
        # Reading in one CSS template:
        if onlyLabel:
            html.append(self.writeContent(LABEL_TEMPLATE_DIR, filename, []))
        elif onlyInfo:
            html.append(self.writeContent(INFO_TEMPLATE_DIR, filename, []))
        else:
            html.append(self.writeContent(FULL_TEMPLATE_DIR, filename, []))
        html.append(u'\n</head>\n<body>\n<p>Your text here...</p>')
        html.append(u'\n<div id="himc-container">')
        if isInfoChecked:
            html.append(u'\n<div id="himc-info-box" class="himc-hidden"></div>')
        if isLabelChecked:
            html.append(u'\n<div class="himc-label"></div>')
        html.append(u'\n<img id="himc-map-container" src="' + imgfilename + '" ')
        html.append(u'border="0" ismap="ismap" usemap="#usemap" alt="HTML imagemap created with QGIS" >')
        html.append(u'\n</div>')
        html.append(u'\n<map name="usemap">\n')
        mapCanvasExtent = self.getTransformedMapCanvas()
        # Now iterate through each feature,
        # select features within current extent,
        # set max progress bar to number of features (not very accurate with a lot of huge multipolygons)
        # or run over all features in current selection, just to determine the number of... (should be simpler ...)
        count = 0
        # With  ALL attributes, WITHIN extent, WITH geom, AND using Intersect instead of bbox
        if hasattr(self.provider, 'select'):
            self.provider.select(self.provider.attributeIndexes(), mapCanvasExtent, True, True)
            while self.provider.nextFeature(feature):
                count = count + 1
        else:
            request = QgsFeatureRequest().setFilterRect(mapCanvasExtent)
            for feature in self.layer.getFeatures(request):
                count = count + 1
        self.htmlImageMapCreatorGui.setProgressBarMax(count)
        progressValue = 0
        # In case of points / lines we need to buffer geometries, calculate bufferdistance here
        bufferDistance = self.iface.mapCanvas().mapUnitsPerPixel() * POINT_AREA_BUFFER
        # Get a list of all selected features ids.
        selectedFeaturesIds = self.layer.selectedFeaturesIds()
        # It seems that a postgres provider is on the end of file now
        if hasattr(self.provider, 'select'):
            self.provider.select(self.provider.attributeIndexes(), mapCanvasExtent, True, True)
            while self.provider.nextFeature(feature):
                # In case of points / lines we need to buffer geometries (plus/minus 20px areas)
                html.extend(self.handleGeom(feature, selectedFeaturesIds, self.doCrsTransform, bufferDistance))
                progressValue = progressValue + 1
                self.htmlImageMapCreatorGui.setProgressBarValue(progressValue)
        else:   # QGIS >= 2.0
            for feature in self.layer.getFeatures(request):
                html.extend(self.handleGeom(feature, selectedFeaturesIds, self.doCrsTransform, bufferDistance))
                progressValue = progressValue + 1
                self.htmlImageMapCreatorGui.setProgressBarValue(progressValue)
        html.append(u'</map>')
        # Write necessary JavaScript content:
        # If only one checkbox is checked, the required code for that feature (label/info box) alone is written
        html.append(u'\n<script type="text/javascript">\n')
        filename = "js.txt"
        # List of replacement parameters for offset-placeholders governing label/info box
        # Reading in one JavaScript template:
        if onlyLabel:
            html.append(self.writeContent(LABEL_TEMPLATE_DIR, filename, [self.label_offset]))
        elif onlyInfo:
            html.append(self.writeContent(INFO_TEMPLATE_DIR, filename, [self.info_offset]))
        else:
            html.append(self.writeContent(FULL_TEMPLATE_DIR, filename, [self.label_offset, self.info_offset]))
        # Dynamically write JavaScript array from field attribute list
        if self.labels:
            html.append(u'\nvar labels = [' + ', '.join(self.labels) + '];')
        if self.info_boxes:
            html.append(u'\nvar infoBoxes = [' + ', '.join(self.info_boxes) + '];')
        # Clean up list afterwards
        del self.labels[:]
        del self.info_boxes[:]
        html.append(u'\n})();')
        html.append(u'\n</script>')
        html.append(u'\n<p>Your text here...</p>')
        html.append(u'\n</body>\n</html>')
        return html

    # Returns a string representing the individual template's content and
    # replaces offset-placeholders in the JavaScript templates
    def writeContent(self, dir, filename, offsets):
        content = ""
        f = codecs.open(u'{}/{}'.format(dir, filename), "r")
        for line in f:
            content += line
        f.close()
        for i, replacement in enumerate("{}".format(i) for i in offsets):
            content = content.replace("{%s}" % i, replacement)
        return content

    def handleGeom(self, feature, selectedFeaturesIds, doCrsTransform, bufferDistance):
        html = []
        # If checkbox 'selectedFeaturesOnly' is checked: check if this feature is selected
        if self.selectedFeaturesOnly and feature.id() not in selectedFeaturesIds:
            return html
        geom = feature.geometry()
        if doCrsTransform:
            if hasattr(geom, "transform"):
                geom.transform(self.crsTransform)
            else:
                QMessageBox.warning(self.iface.mainWindow(), self.MSG_BOX_TITLE, (
                  "Cannot crs-transform geometry in your version of QGIS ...\n"
                  "Only QGIS version 1.5 and above can transform geometries on the fly\n"
                  "As a workaround, you can try to save the layer in the destination crs\n"
                  "(eg as shapefile) and reload that layer...\n"), QMessageBox.Ok, QMessageBox.Ok)
                raise Exception("Cannot crs-transform geometry in your QGIS version ...\n"
                  "Only QGIS version 1.5 and above can transform geometries on the fly\n"
                  "As a workaround, you can try to save the layer in the destination crs\n"
                  "(e.g. as Shapefile) and reload that layer...\n")
        projectExtent = self.iface.mapCanvas().extent()
        projectExtentAsPolygon = QgsGeometry()
        projectExtentAsPolygon = QgsGeometry.fromRect(projectExtent)
        if geom.wkbType() == QGis.WKBPoint:  # 1 = WKBPoint
            # We make a copy of the geom, because apparently buffering the original will
            # only buffer the source-coordinates
            geomCopy = QgsGeometry.fromPoint(geom.asPoint())
            polygon = geomCopy.buffer(bufferDistance, 0).asPolygon()
            html.append(self.polygon2html(feature, projectExtent, projectExtentAsPolygon, polygon))
        if geom.wkbType() == QGis.WKBMultiPoint:  # 4 = WKBMultiPoint
            multipoint = geom.asMultiPoint()
            for point in multipoint:  # returns a list
                geomCopy = QgsGeometry.fromPoint(point)
                polygon = geomCopy.buffer(bufferDistance, 0).asPolygon()
                html.append(self.polygon2html(feature, projectExtent, projectExtentAsPolygon, polygon))
        if geom.wkbType() == QGis.WKBPolygon:  # 3 = WKBPolygon:
            polygon = geom.asPolygon()  # returns a list
            html.append(self.polygon2html(feature, projectExtent, projectExtentAsPolygon, polygon))
        if geom.wkbType() == QGis.WKBMultiPolygon:  # 6 = WKBMultiPolygon:
            multipolygon = geom.asMultiPolygon()  # returns a list
            for polygon in multipolygon:
                html.append(self.polygon2html(feature, projectExtent, projectExtentAsPolygon, polygon))
        return html

    def polygon2html(self, feature, projectExtent, projectExtentAsPolygon, polygon):
        area_strings = [self.ring2html(feature, ring, projectExtent, projectExtentAsPolygon) for ring in polygon]
        return "".join(area_strings)

    def renderTest(self, painter):
        # Get canvas dimensions
        self.canvas_width = painter.device().width()
        self.canvas_height = painter.device().height()

    # SIGNAL slots:
    def setFilesPath(self, filesPathQString):
        self.files_path = filesPathQString

    def setCurrentFilesPath(self, filesPathQString):
        self.current_filename = filesPathQString
        self.isReady()

    def setLayerName(self, layerNameQString):
        self.layer_name = layerNameQString

    def setFeatureTotal(self, totalQString):
        self.feature_total = totalQString

    def setDimensions(self, dimensionsQString):
        self.dimensions = dimensionsQString

    def labelAttributeFieldSet(self, attributeFieldQstring):
        self.labelAttributeField = attributeFieldQstring
        self.label_field_index = self.layer.fieldNameIndex(attributeFieldQstring)

    def setLabelOffset(self, offset):
        self.label_offset = offset

    def setLabelChecked(self, isChecked):
        self.label_checked = isChecked

    def setCurrentLabelState(self, isChecked):
        self.label_currently_checked = isChecked
        self.isReady()

    def infoBoxAttributeFieldSet(self, attributeFieldQstring):
        self.infoBoxAttributeField = attributeFieldQstring
        self.info_field_index = self.layer.fieldNameIndex(attributeFieldQstring)

    def setInfoOffset(self, offset):
        self.info_offset = offset

    def setInfoChecked(self, isChecked):
        self.info_checked = isChecked

    def setCurrentInfoState(self, isChecked):
        self.info_currently_checked = isChecked
        self.isReady()

    def setSelectedOnly(self, selectedOnlyBool):
        self.selectedFeaturesOnly = selectedOnlyBool

    def setFeatureCount(self, featureCountQstring):
        self.feature_count = featureCountQstring

    def setMapCanvasSize(self, newWidth, newHeight):
        mapCanvas = self.iface.mapCanvas()
        parent = mapCanvas.parentWidget()
        # QGIS 2.4 places another widget between mapcanvas and qmainwindow, so:
        if not parent.parentWidget() is None:
            parent = parent.parentWidget()
        # Some QT magic for me, coming from maximized, force a minimal layout change first
        if(parent.isMaximized()):
            QMessageBox.warning(self.iface.mainWindow(), self.MSG_BOX_TITLE, (
              "Maximized QGIS window..\n"
              "QGIS window is maximized, plugin will try to de-maximize the window.\n"
              "If image size is still not exact what you asked for,\n"
              "try starting plugin with non maximized window."), QMessageBox.Ok, QMessageBox.Ok)
            parent.showNormal()
        diffWidth = mapCanvas.size().width() - newWidth
        diffHeight = mapCanvas.size().height() - newHeight
        mapCanvas.resize(newWidth, newHeight)
        parent.resize(parent.size().width() - diffWidth, parent.size().height() - diffHeight)
        # HACK: There are cases where after maximizing and here demaximizing the size of the parent is not
        # in sync with the actual size, giving a small error in the size setting.
        # We do the resizing again, which fixes this small error
        if newWidth != mapCanvas.size().width() or newHeight != mapCanvas.size().height():
            diffWidth = mapCanvas.size().width() - newWidth
            diffHeight = mapCanvas.size().height() - newHeight
            mapCanvas.resize(newWidth, newHeight)
            parent.resize(parent.size().width() - diffWidth, parent.size().height() - diffHeight)

    def go(self, foo):
        htmlfilename = unicode(self.files_path + ".html")
        imgfilename = unicode(self.files_path + ".png")
        if os.path.isfile(htmlfilename) or os.path.isfile(imgfilename):
            if QMessageBox.question(self.iface.mainWindow(), self.MSG_BOX_TITLE, (
              "There is already a filename with this name.\n" "Continue?"),
              QMessageBox.Cancel, QMessageBox.Ok) == QMessageBox.Cancel:
                return
        # Else: everthing ok: start writing img and html
        try:
            if len(self.files_path) == 0:
                raise IOError
            file = open(htmlfilename, "w")
            html = self.writeHtml()
            for line in html:
                file.write(line.encode('utf-8'))
            file.close()
            self.area_index = 0
            self.iface.mapCanvas().saveAsImage(imgfilename)
            msg = "Files successfully saved to:\n" + self.files_path
            QMessageBox.information(self.iface.mainWindow(), self.MSG_BOX_TITLE, msg, QMessageBox.Ok)
            self.htmlImageMapCreatorGui.hide()
        except IOError:
            QMessageBox.warning(self.iface.mainWindow(), self.MSG_BOX_TITLE, (
              "Invalid path.\n"
              "Path does either not exist or is not writable."), QMessageBox.Ok, QMessageBox.Ok)
        finally:
            # Remember layer id:
            self.layer_id = self.layer.id()

    def world2pixel(self, x, y, mupp, minx, maxy):
        pixX = (x - minx)/mupp
        pixY = (y - maxy)/mupp
        return [int(pixX), int(-pixY)]

    # For given ring in feature, IF at least one point in ring is in 'mapCanvasExtent',
    # generate a string like:
    # <area data-info-id=x shape="poly" coords=519,-52,519,..,-52,519,-52 alt=...>
    def ring2html(self, feature, ring, extent, extentAsPoly):
        param = u''
        html_tmp = u'<area data-info-id="{}" shape="poly" '.format(self.area_index)
        self.area_index = self.area_index + 1
        if hasattr(feature, 'attributeMap'):
            attrs = feature.attributeMap()
        else:
            # QGIS > 2.0
            attrs = feature
        # Escape ' and " because they will collapse as JavaScript parameter
        if self.htmlImageMapCreatorGui.isInfoBoxChecked():
            param = unicode(attrs[self.info_field_index])
            self.info_boxes.append(json.dumps(param))
        if self.htmlImageMapCreatorGui.isLabelChecked():
            param = unicode(attrs[self.label_field_index])
            self.labels.append(json.dumps(param))
        html_tmp = html_tmp + 'coords="'
        lastPixel = [0, 0]
        insideExtent = False
        coordCount = 0
        extentAsPoly = QgsGeometry()
        extentAsPoly = QgsGeometry.fromRect(extent)
        for point in ring:
            if extentAsPoly.contains(point):
                insideExtent = True
            pixpoint = self.world2pixel(point.x(), point.y(),
                           self.iface.mapCanvas().mapUnitsPerPixel(),
                           extent.xMinimum(), extent.yMaximum())
            if lastPixel != pixpoint:
                coordCount = coordCount + 1
                html_tmp += (str(pixpoint[0]) + ',' + str(pixpoint[1]) + ',')
                lastPixel = pixpoint
        html_tmp = html_tmp[0:-1]
        # Check if there are more than 2 coords: very small polygons on current map can have coordinates,
        # which if rounded to pixels all come to the same pixel, resulting in just ONE x,y coordinate.
        # We skip these:
        if coordCount < 2:
            return ''
        # If at least ONE pixel of this ring is in current view extent,
        # return the area-string, otherwise return an empty string
        if not insideExtent:
            return ''
        else:
            # Using last param (labels) as alt parameter (to be W3 compliant we need one).
            # If only info-boxes are selected, the area-index is used as an alternative
            alt = json.dumps(param) if self.htmlImageMapCreatorGui.isLabelChecked() else u'{}'.format(self.area_index)
            html_tmp += '" alt=' + alt + '>\n'
            return unicode(html_tmp)

    # Transforms the coordinates of the current map canvas extent, so that it can then be used
    # for geometric checks and as a filter
    def getTransformedMapCanvas(self):
        mapCanvasExtent = self.iface.mapCanvas().extent()
        self.doCrsTransform = False
        # In case of 'on the fly projection'.
        # Different srs's for mapCanvas/project and layer we have to reproject stuff
        if hasattr(self.iface.mapCanvas().mapSettings(), 'destinationSrs'):
            # QGIS < 2.0
            destinationCrs = self.iface.mapCanvas().mapSettings().destinationSrs()
            layerCrs = self.layer.srs()
        else:
            destinationCrs = self.iface.mapCanvas().mapSettings().destinationCrs()
            layerCrs = self.layer.crs()
        if not destinationCrs == layerCrs:
            # We have to transform the mapCanvasExtent to the data/layer Crs to be able
            # to retrieve the features from the data provider,
            # but ONLY if we are working with on the fly projection.
            # (because in that case we just 'fly' to the raw coordinates from data)
            if self.iface.mapCanvas().hasCrsTransformEnabled():
                self.crsTransform = QgsCoordinateTransform(destinationCrs, layerCrs)
                mapCanvasExtent = self.crsTransform.transformBoundingBox(mapCanvasExtent)
                # We have to have a transformer to do the transformation of the geometries
                # to the mapcanvas srs ourselves:
                self.crsTransform = QgsCoordinateTransform(layerCrs, destinationCrs)
                self.doCrsTransform = True
        return mapCanvasExtent

    # Returns a list of bounding boxes, which represents the original geometries
    def geom2rect(self, geom):
        if geom.wkbType() == QGis.WKBPoint:  # 1 = WKBPoint
            return [geom.boundingBox()]
        if geom.wkbType() == QGis.WKBMultiPoint:  # 4 = WKBMultiPoint
            multipoint = geom.asMultiPoint()
            return [geom.boundingBox() for point in multipoint]
        if geom.wkbType() == QGis.WKBPolygon:  # 3 = WKBPolygon:
            return [geom.boundingBox()]
        if geom.wkbType() == QGis.WKBMultiPolygon:  # 6 = WKBMultiPolygon:
            multipolygon = geom.asMultiPolygon()
            return [geom.boundingBox() for polygon in multipolygon]

    # Returns the number of *selected* features ((multi-)points/(multi-)polygons)
    # within the current map view
    def nofSelectedFeaturesInExtent(self):
        count = 0
        mapCanvasExtent = self.getTransformedMapCanvas()
        iter = self.layer.selectedFeatures()
        for feature in iter:
            geom = feature.geometry()
            if not geom is None:
                for rect in self.geom2rect(geom):
                    if mapCanvasExtent.intersects(rect):
                        count = count + 1
        return count
