from PySide2.QtWidgets import QApplication, QWidget
from qimage2ndarray import array2qimage

import numpy as np

from sensorbasedAO.gui.ui.SHViewer import Ui_SHViewer
from sensorbasedAO.config import config

class SHViewer(QWidget):
    """
    SH viewer class
    """

    def __init__(self, parent = None, dtype = 'uint8'):
        super().__init__(parent)

        # Set up and initialise Ui_SHViewer class
        self.ui = Ui_SHViewer()
        self.ui.setupUi(self)

        # Initialise image array and datatype
        self.dtype = dtype
        self.sensor_width = config['camera']['sensor_width'] // config['camera']['bin_factor']
        self.sensor_height = config['camera']['sensor_height'] // config['camera']['bin_factor']
        self.array_raw_img, self.array_raw_SB, self.array, self.img_array = \
            (np.zeros([self.sensor_height, self.sensor_width]) for i in range(4))

        # Get image display settings
        self.settings = self.get_settings()

        # Display image on image viewer
        self.ui.graphicsView.setImage(array2qimage(self.array), reset = False)

    def get_settings(self):
        settings = {}
        settings['normalise'] = config['image']['normalise']
        settings['rescale'] = config['image']['rescale']
        settings['norm_min'] = config['image']['norm_min'] 
        settings['norm_max'] = config['image']['norm_max']
        settings['data_min'] = config['camera']['data_min']
        settings['data_max'] = config['camera']['data_max']

        return settings

    #==========Methods==========#
    def set_image(self, array, flag = 0, SB_settings = None):
        """
        Conditions the image for display on S-H viewer

        Args: 
            array as numpy array
            flag = 0 for search block layer display
            flag = 1 for S-H spot image display
        """
        # Set raw data array
        if flag:
            self.array_raw_img = array.copy()
        else:
            self.array_raw_SB = array.copy()

        # Update image display settings
        if flag:
            if 'act_SB_coord' in SB_settings:
                self.array_raw_SB.ravel()[SB_settings['act_SB_coord']] = config['search_block']['outline_int']
            self.array = self.array_raw_img.copy() + self.array_raw_SB.copy()
            self.update()
        else:
            self.array = self.array_raw_SB.copy()
            self.update()

        # Display image on image viewer
        self.ui.graphicsView.setImage(array2qimage(self.array))

    def update(self):
        """
        Normalises and rescales image
        """
        # Get image display settings
        settings = self.get_settings()

        # Get minimum and maximum pixel clipping value
        scale_min = np.interp(settings['norm_min'], \
            (np.iinfo(self.dtype).min, np.iinfo(self.dtype).max), \
                (settings['data_min'], settings['data_max']))
        scale_max = np.interp(settings['norm_max'], \
            (np.iinfo(self.dtype).min, np.iinfo(self.dtype).max), \
                (settings['data_min'], settings['data_max']))

        # Clip array to scale_min and scale_max
        self.array = np.clip(self.array, scale_min, scale_max)

        # Rescale to image viewer dtype
        if settings['rescale']:
            self.array = np.interp(self.array, (self.array.min(), self.array.max()), \
                (np.iinfo(self.dtype).min, np.iinfo(self.dtype).max)).astype(self.dtype)
        elif settings['normalise']:
            self.array = np.interp(self.array, (self.array.min(), self.array.max()), \
                (settings['norm_min'], settings['norm_max'])).astype(self.dtype)
        else:
            self.array = np.interp(self.array, (scale_min, scale_max), \
                (np.iinfo(self.dtype).min, np.iinfo(self.dtype).max)).astype(self.dtype)


if __name__ == '__main__':
    import sys
 
    app = QApplication(sys.argv)
    SH_viewer = SHViewer()
    SH_viewer.show()
    SH_viewer.set_image(np.random.randint(0,10,size = (100, 100)))
    sys.exit(app.exec_())