from ximea import xiapi

import imageio
import numpy as np

import log
from config import config

logger = log.get_logger(__name__)


class SENSOR_XIMEA(xiapi.Camera):
    """
    Create an instance of the Camera class to access its methods, attributes, and the target device at serial port.
    """
    def __init__(self):
        # Create Camera instance
        self.sensor = xiapi.Camera()

        # Open camera by serial number
        self.sensor.open_device_by_SN(config['camera']['SN'])
        
        # Set camera image format
        self.sensor.set_imgdataformat(config['camera']['dataformat'])

        # Set camera exposure and gain preference
        if config['camera']['auto_gain'] == 0:
            self.sensor.set_exposure(config['camera']['exposure'])
        else:
            self.sensor.enable_aeag()
            self.sensor.set_exp_priority(config['camera']['exp_priority'])
            self.sensor.set_ae_max_limit(config['camera']['ae_max_limit'])

        # Set camera frame acquisition mode       
        if config['camera']['burst_mode'] == 1:
            self.sensor.set_trigger_selector('XI_TRG_SEL_FRAME_BURST_START')
            self.sensor.set_acq_frame_burst_count(config['camera']['burst_frames'])
        else:
            self.sensor.set_acq_timing_mode(config['camera']['acq_timing_mode'])
            self.sensor.set_framerate(config['camera']['frame_rate'])

        # Set camera trigger mode
        self.sensor.set_trigger_source(config['camera']['trigger_source'])

        super().__init__()


class SENSOR():
    """
    Sensor controller factory class, returns sensor instances of appropriate type.
    """
    def __init__(self):
        logger.info('Sensor factory class loaded')

    @staticmethod
    def get(type = config['camera']['SN']):
        if type.lower() == 'ximea':
            try:
                sensor = SENSOR_XIMEA()
            except:
                logger.warning('Unable to load Ximea camera')
        elif type.lower() == 'debug':
            sensor = SENSOR_dummy()
        elif type.lower() == 'image':
            sensor = SENSOR_image(config['camera']['image'])
        else:
            sensor = None

        return sensor


class SENSOR_dummy():
    def __init__(self):
        logger.info('Dummy sensor loaded')

    def set_trigger_software(self, flag = True):
        pass

    def get_image(self, img, timeout = 1000):
        pass

class SENSOR_image():
    def __init__(self, image_path):
        logger.info('Dummy sensor loaded')
        # self._image = np.zeros([config['camera']['sensor_height'],config['camera']['sensor_width']])
        image = imageio.imread(image_path)
        image = np.dot(image[... , :3] , [0.299 , 0.587, 0.114]) 
        print(image.shape)
        print(image.shape)
        self.set_image(image)

    def set_trigger_software(self, flag = True):
        pass

    def get_image(self, timeout = 1000):
        return self._image.copy()

    def open_device_by_SN(self, SN):
        pass

    def start_acquisition(self):
        pass

    def stop_acquisition(self):
        pass
    
    def set_image(self, image):
        if image.shape[0] != image.shape[1]:
            height, width = image.shape
            min_dimension = min(height, width)
            centre_x = width//2
            centre_y = height//2
            min_x = centre_x-min_dimension//2
            max_x = centre_x+min_dimension//2
            min_y = centre_y-min_dimension//2
            max_y = centre_y+min_dimension//2
            image = image[min_y:max_y,min_x:max_x]

        self._image = image.copy()
        print('set_image)asdfasd', self._image.shape)

    def acquire_image(self, height, width, acq_mode=0):
        # Create instance of dataimage array and data list to store image data
        dataimage = np.zeros([config['camera']['sensor_height'], config['camera']['sensor_width']])
        image_width = int(config['camera']['sensor_width'] // config['camera']['bin_factor'])
        image_height = int(config['camera']['sensor_height'] // config['camera']['bin_factor'])
        data = np.zeros([image_height, image_width, config['camera']['frame_num']])
        
        # Create instance of Ximea Image to store image data and metadata
        if acq_mode == 0:
            dataimage = self.get_image()

        elif acq_mode == 1:

            # Acquire a sequence of images and append to data list
            for i in range(config['camera']['frame_num']):

                # Append dataimage to data
                data[:, :, i] = self.get_image()
            
        if acq_mode == 0:
            return dataimage
        elif acq_mode == 1:
            return data
        else:
            return None