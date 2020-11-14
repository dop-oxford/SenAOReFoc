from PySide2.QtCore import QThread, QObject, Signal, Slot
from PySide2.QtWidgets import QApplication

import logging
import sys
import os
import argparse
import time
import h5py
from scipy import io
import numpy as np
import scipy as sp

import log
from config import config
from HDF5_dset import dset_append, get_dset
from image_acquisition import acq_image
from centroid_acquisition import acq_centroid
from gaussian_inf import inf
from common import fft_spot_from_phase
from zernike_phase import zern_phase
from reflectance_process import reflect_process

logger = log.get_logger(__name__)

class AO_Zernikes_Test(QObject):
    """
    Tests closed-loop AO via zernikes / slopes for ideal / DM control matrix generated Zernike modes (iterates through each mode)
    """
    start = Signal()
    write = Signal()
    done = Signal()
    error = Signal(object)
    image = Signal(object)
    message = Signal(object)
    info = Signal(object)

    # def __init__(self, sensor, mirror, scanner, settings):
    def __init__(self, sensor, mirror, settings):

        # Get search block settings
        self.SB_settings = settings['SB_info']

        # Get mirror settings
        self.mirror_settings = settings['mirror_info']

        # Get AO settings
        self.AO_settings = settings['AO_info']

        # Get sensor instance
        self.sensor = sensor

        # Get mirror instance
        self.mirror = mirror

        # Get scanner instance
        # self.scanner = scanner

        # Initialise AO information parameter
        self.AO_info = {'zern_test': {}}

        # Initialise zernike coefficient array
        self.zern_coeff = np.zeros([config['AO']['control_coeff_num'], 1])

        # Choose working DM along with its parameters
        if config['DM']['DM_num'] == 0:
            self.actuator_num = config['DM0']['actuator_num']
            self.pupil_diam = config['search_block']['pupil_diam_0']
        elif config['DM']['DM_num'] == 1:
            self.actuator_num = config['DM1']['actuator_num']
            self.pupil_diam = config['search_block']['pupil_diam_1']
        
        super().__init__()

    def strehl_calc(self, phase):
        """
        Calculates the strehl ratio of a given phase profile
        """
        # Get meshgrid of coordinates within phase image
        coord_xx, coord_yy = self.get_coord_mesh(self.SB_settings['sensor_width'])

        # Get boolean pupil mask
        pupil_mask = self.get_pupil_mask(coord_xx, coord_yy)

        # Get average phase and phase deviation across pupil aperture
        phase_ave = np.mean(phase[pupil_mask])
        phase_delta = (phase - phase_ave) * pupil_mask

        # print('Max and min values in phase before subtracting average phase: {}, {}'.format(np.amax(phase), np.amin(phase)))
        # print('Max and min values in phase after subtracting average phase: {}, {}'.format(np.amax(phase_delta), np.amin(phase_delta)))
        # print('Average phase value is:', phase_ave)

        # Calculate Strehl ratio estimated using only the statistics of the phase deviation, according to Mahajan
        phase_delta_2 = phase_delta ** 2 * pupil_mask
        sigma_2 = np.mean(phase_delta_2[pupil_mask])
        strehl = np.exp(-(2 * np.pi / config['AO']['lambda']) ** 2 * sigma_2)

        return strehl

    def phase_calc(self, voltages):
        """
        Calculates phase profile introduced by DM
        """
        # Get meshgrid of coordinates within phase image
        coord_xx, coord_yy = self.get_coord_mesh(self.SB_settings['sensor_width'])

        # Get boolean pupil mask
        pupil_mask = self.get_pupil_mask(coord_xx, coord_yy)

        # Calculate Gaussian distribution influence function value introduced by each actuator at each pixel
        delta_phase = np.zeros([self.SB_settings['sensor_width'], self.SB_settings['sensor_width']])

        for i in range(self.actuator_num):
            delta_phase += inf(coord_xx, coord_yy, self.mirror_settings['act_pos_x'], self.mirror_settings['act_pos_y'],\
                i, self.mirror_settings['act_diam']) * voltages[i]

        delta_phase = delta_phase * pupil_mask

        # print('Max and min values in delta_phase are: {} um, {} um'.format(np.amax(delta_phase), np.amin(delta_phase)))

        return delta_phase

    def get_coord_mesh(self, image_diam):
        """
        Retrieves coordinate meshgrid for calculations over a full image
        """
        coord_x, coord_y = (np.arange(int(-image_diam / 2), int(-image_diam / 2) + image_diam) for i in range(2))
        coord_xx, coord_yy = np.meshgrid(coord_x, coord_y)

        return coord_xx, coord_yy

    def get_pupil_mask(self, coord_xx, coord_yy):
        """
        Retrieves boolean pupil mask for round aperture
        """
        pupil_mask = np.sqrt((coord_xx * self.SB_settings['pixel_size']) ** 2 + \
            (coord_yy * self.SB_settings['pixel_size']) ** 2) <= self.pupil_diam * 1e3 / 2
        
        return pupil_mask

    @Slot(object)
    def run0(self):
        try:
            # Set process flags
            self.loop = True 
            self.log = True

            # Start thread
            self.start.emit()

            """
            Iterate through each zernike mode aberration with the same amplitude and perform closed-loop AO via zernikes for a fixed
            number of loops 
            """
            # Initialise arrays for storing Strehl ratios, rms wavefront errors and loop numbers
            self.zern_num = config['AO']['control_coeff_num'] - 2
            self.strehl = np.zeros([2, self.zern_num])
            self.loop_rms_zern, self.loop_rms_zern_part = (np.zeros([self.AO_settings['loop_max'] + 1, self.zern_num]) for i in range(2))
            self.loop_num = np.zeros(self.zern_num)

            self.message.emit('\nProcess started for closed-loop AO via Zernikes...')

            # Initialise deformable mirror voltage array
            voltages = np.zeros(self.actuator_num)

            prev1 = time.perf_counter()

            for n in range(config['zern_test']['run_num']):

                # Run closed-loop control for each zernike mode aberration
                for k in range(config['zern_test']['incre_num']):

                    # Initialise AO information parameter
                    self.AO_info = {'zern_test': {}}

                    # Create new datasets in HDF5 file to store closed-loop AO data and open file
                    get_dset(self.SB_settings, 'zern_test', flag = 0)
                    data_file = h5py.File('data_info.h5', 'a')
                    data_set_1 = data_file['AO_img']['zern_test']
                    data_set_2 = data_file['AO_info']['zern_test']

                    # Initialise array to store initial and final detected value of each generated Zernike mode
                    self.det_cor_zern_coeff = np.zeros([self.zern_num * 2, self.zern_num])

                    # Determine the amplitude to be generated for each Zernike mode
                    zern_amp_gen = config['zern_test']['incre_amp'] * (k + 1)

                    # Determine initial loop gain for generation of each Zernike mode
                    if zern_amp_gen <= 0.1:
                        loop_gain_gen = 0.1
                    elif zern_amp_gen > 0.1 and zern_amp_gen <= 0.2:
                        loop_gain_gen = 0.2
                    elif zern_amp_gen > 0.2:
                        loop_gain_gen = 0.3

                    for j in range(self.zern_num):

                        print('On amplitude {} Zernike mode {}'.format(k + 1, j + 3))

                        for i in range(self.AO_settings['loop_max'] + 1):
                            
                            if self.loop:
                                
                                try:

                                    # Update mirror control voltages
                                    if i == 0:

                                        if not config['dummy']:

                                            # Generate one Zernike mode on DM for correction each time
                                            self.zern_coeff[j + 2, 0] = zern_amp_gen

                                            # Run closed-loop to generate a precise amount of Zernike modes using DM
                                            for m in range(config['zern_test']['loop_max_gen']):

                                                if m == 0:

                                                    voltages[:] = config['DM']['vol_bias']

                                                else:

                                                    # Use smaller loop gain towards latter half of iteration
                                                    if m >= config['zern_test']['loop_max_gen'] // 2:
                                                        loop_gain_gen = 0.1

                                                    # Update control voltages
                                                    voltages -= loop_gain_gen * np.ravel(np.dot(self.mirror_settings['control_matrix_zern']\
                                                        [:,:config['AO']['control_coeff_num']], (zern_array_det[:config['AO']['control_coeff_num']] - self.zern_coeff)))

                                                # Send values vector to mirror
                                                self.mirror.Send(voltages)

                                                # Wait for DM to settle
                                                time.sleep(config['DM']['settling_time'])
                                            
                                                # Acquire S-H spots using camera
                                                AO_image_stack = acq_image(self.sensor, self.SB_settings['sensor_height'], self.SB_settings['sensor_width'], acq_mode = 1)
                                                AO_image = np.mean(AO_image_stack, axis = 2)

                                                # Image thresholding to remove background
                                                AO_image = AO_image - config['image']['threshold'] * np.amax(AO_image)
                                                AO_image[AO_image < 0] = 0
                                                self.image.emit(AO_image)

                                                # Append image to list
                                                dset_append(data_set_1, 'real_AO_img', AO_image)

                                                # Calculate centroids of S-H spots
                                                act_cent_coord, act_cent_coord_x, act_cent_coord_y, slope_x, slope_y = acq_centroid(self.SB_settings, flag = 2)
                                                act_cent_coord, act_cent_coord_x, act_cent_coord_y = map(np.asarray, [act_cent_coord, act_cent_coord_x, act_cent_coord_y])

                                                # Draw actual S-H spot centroids on image layer
                                                AO_image.ravel()[act_cent_coord.astype(int)] = 0
                                                self.image.emit(AO_image)

                                                # Take tip\tilt off
                                                slope_x -= np.mean(slope_x)
                                                slope_y -= np.mean(slope_y)

                                                # Concatenate slopes into one slope matrix
                                                slope = (np.concatenate((slope_x, slope_y), axis = 1)).T

                                                # Get detected zernike coefficients from slope matrix
                                                zern_array_det = np.dot(self.mirror_settings['conv_matrix'], slope)

                                                # print('Detected amplitude of mode {} is {} um'.format(m + 3, zern_array_det[m + 2]))
                                        else:

                                            voltages[:] = config['DM']['vol_bias']                              
                                    else:

                                        voltages -= config['AO']['loop_gain'] * np.ravel(np.dot(self.mirror_settings['control_matrix_zern']\
                                            [:,:config['AO']['control_coeff_num']], zern_err[:config['AO']['control_coeff_num']]))

                                        print('Max and min values of voltages {} are: {}, {}'.format(i, np.max(voltages), np.min(voltages)))

                                    if config['dummy']:

                                        # Update phase profile and retrieve S-H spot image 
                                        if i == 0:

                                            # Option 1: Generate real zernike phase profile using DM control matrix
                                            if config['real_zernike']:

                                                # Generate input zernike coefficient array
                                                self.zern_coeff[j + 2] = config['zern_test']['incre_amp'] * (k + 1)

                                                # Retrieve actuator voltages from zernike coefficient array
                                                voltages = np.ravel(np.dot(self.mirror_settings['control_matrix_zern']\
                                                    [:,:config['AO']['control_coeff_num']], self.zern_coeff))
                                                
                                                # Generate zernike phase profile from DM
                                                phase_init = self.phase_calc(voltages)

                                                # Check whether need to incorporate sample reflectance process
                                                if config['reflect_on'] == 1:
                                                    phase_init = reflect_process(self.SB_settings, phase_init, self.pupil_diam)
                                                
                                            # Option 2: Generate ideal zernike phase profile
                                            else:
                                                
                                                # Generate input zernike coefficient array
                                                self.zern_coeff[j + 2] = config['zern_test']['incre_amp'] * (k + 1)
                                                
                                                # Generate ideal zernike phase profile
                                                phase_init = zern_phase(self.SB_settings,  self.zern_coeff)

                                                # Check whether need to incorporate sample reflectance process
                                                if config['reflect_on'] == 1:
                                                    phase_init = reflect_process(self.SB_settings, phase_init, self.pupil_diam) 

                                            # Display initial phase
                                            self.image.emit(phase_init)

                                            # print('\nMax and min values of phase {} are: {} um, {} um'.format(i, np.amax(phase_init), np.amin(phase_init)))

                                            # Get simulated S-H spots and append to list
                                            AO_image, spot_cent_x, spot_cent_y = fft_spot_from_phase(self.SB_settings, phase_init)
                                            dset_append(data_set_1, 'dummy_AO_img', AO_image)
                                            dset_append(data_set_1, 'dummy_spot_cent_x', spot_cent_x)
                                            dset_append(data_set_1, 'dummy_spot_cent_y', spot_cent_y)
                                            
                                            phase = phase_init.copy()

                                        else:

                                            # Calculate phase profile introduced by DM
                                            delta_phase = self.phase_calc(voltages)

                                            # Update phase data
                                            phase = phase_init - delta_phase

                                            # Display corrected phase
                                            self.image.emit(phase)

                                            # print('Max and min values of phase {} are: {} um, {} um'.format(i, np.amax(phase), np.amin(phase)))

                                            # Get simulated S-H spots and append to list
                                            AO_image, spot_cent_x, spot_cent_y = fft_spot_from_phase(self.SB_settings, phase)
                                            dset_append(data_set_1, 'dummy_AO_img', AO_image)
                                            dset_append(data_set_1, 'dummy_spot_cent_x', spot_cent_x)
                                            dset_append(data_set_1, 'dummy_spot_cent_y', spot_cent_y)

                                    else:

                                        # Send values vector to mirror
                                        self.mirror.Send(voltages)
                                        
                                        # Wait for DM to settle
                                        time.sleep(config['DM']['settling_time'])
                                    
                                        # Acquire S-H spots using camera
                                        AO_image_stack = acq_image(self.sensor, self.SB_settings['sensor_height'], self.SB_settings['sensor_width'], acq_mode = 1)
                                        AO_image = np.mean(AO_image_stack, axis = 2)

                                    # Image thresholding to remove background
                                    AO_image = AO_image - config['image']['threshold'] * np.amax(AO_image)
                                    AO_image[AO_image < 0] = 0
                                    self.image.emit(AO_image)

                                    # Append image to list
                                    dset_append(data_set_1, 'real_AO_img', AO_image)

                                    # Calculate centroids of S-H spots
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y, slope_x, slope_y = acq_centroid(self.SB_settings, flag = 2) 
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y = map(np.asarray, [act_cent_coord, act_cent_coord_x, act_cent_coord_y])

                                    # Draw actual S-H spot centroids on image layer
                                    AO_image.ravel()[act_cent_coord.astype(int)] = 0
                                    self.image.emit(AO_image)

                                    # Take tip\tilt off
                                    slope_x -= np.mean(slope_x)
                                    slope_y -= np.mean(slope_y)

                                    # Concatenate slopes into one slope matrix
                                    slope = (np.concatenate((slope_x, slope_y), axis = 1)).T

                                    # Get detected zernike coefficients from slope matrix
                                    self.zern_coeff_detect = np.dot(self.mirror_settings['conv_matrix'], slope)

                                    if i == 0:
                                        self.det_cor_zern_coeff[2 * j, :] = self.zern_coeff_detect[2:config['AO']['control_coeff_num'], 0].T

                                    # Get phase residual (zernike coefficient residual error) and calculate root mean square (rms) error
                                    zern_err = self.zern_coeff_detect.copy()
                                    zern_err_part = self.zern_coeff_detect.copy()
                                    zern_err_part[[0, 1], 0] = 0
                                    rms_zern = np.sqrt((zern_err ** 2).sum())
                                    rms_zern_part = np.sqrt((zern_err_part ** 2).sum())
                                    self.loop_rms_zern[i,j] = rms_zern
                                    self.loop_rms_zern_part[i,j] = rms_zern_part

                                    strehl = np.exp(-(2 * np.pi / config['AO']['lambda'] * rms_zern_part) ** 2)
                                    if config['dummy']:
                                        strehl_2 = self.strehl_calc(phase)

                                    print('Full zernike root mean square error {} is {} um'.format(i, rms_zern))

                                    # Append data to list
                                    if config['dummy']:
                                        dset_append(data_set_2, 'dummy_spot_slope_x', slope_x)
                                        dset_append(data_set_2, 'dummy_spot_slope_y', slope_y)
                                        dset_append(data_set_2, 'dummy_spot_slope', slope)
                                        dset_append(data_set_2, 'dummy_spot_zern_err', zern_err)
                                    else:
                                        dset_append(data_set_2, 'real_spot_slope_x', slope_x)
                                        dset_append(data_set_2, 'real_spot_slope_y', slope_y)
                                        dset_append(data_set_2, 'real_spot_slope', slope)
                                        dset_append(data_set_2, 'real_spot_zern_err', zern_err)

                                    # Compare rms error with tolerance factor (Marechel criterion) and decide whether to break from loop
                                    if strehl >= config['AO']['tolerance_fact_strehl'] or i == self.AO_settings['loop_max']:
                                        self.strehl[0,j] = strehl
                                        if config['dummy']:
                                            self.strehl[1,j] = strehl_2
                                        self.loop_num[j] = i
                                        self.zern_coeff[j + 2] = 0
                                        self.det_cor_zern_coeff[2 * j + 1, :] = self.zern_coeff_detect[2:config['AO']['control_coeff_num'], 0].T
                                        break                 

                                except Exception as e:
                                    print(e)
                            else:

                                self.done.emit()

                    # Save detected aberrations to file
                    sp.io.savemat('zern_gen_det_cor/zernike_correction/amp_' + str(config['zern_test']['incre_amp'] * (k + 1)) + 'run_' + str(n) + '.mat',\
                        dict(zern_det_cor_zern_amp = self.det_cor_zern_coeff))

                    # Close HDF5 file
                    data_file.close()

            self.message.emit('\nProcess complete.')

            prev2 = time.perf_counter()
            print('Time for closed-loop AO process is:', (prev2 - prev1))

            """
            Returns closed-loop AO information into self.AO_info
            """             
            if self.log:
                
                self.AO_info['zern_test']['loop_num'] = self.loop_num
                self.AO_info['zern_test']['residual_phase_err_zern'] = self.loop_rms_zern
                self.AO_info['zern_test']['residual_phase_err_zern_part'] = self.loop_rms_zern_part
                self.AO_info['zern_test']['strehl_ratio'] = self.strehl

                self.info.emit(self.AO_info)
                self.write.emit()
            else:

                self.done.emit()

            # Finished closed-loop AO process
            self.done.emit()

        except Exception as e:
            raise
            self.error.emit(e)

    @Slot(object)
    def run1(self):
        try:
            # Set process flags
            self.loop = True 
            self.log = True

            # Start thread
            self.start.emit()

            """
            Iterate through each zernike mode aberration with the same amplitude and perform closed-loop AO via slopes for a fixed
            number of loops 
            """
            # Initialise arrays for storing Strehl ratios, rms wavefront errors and loop numbers
            self.zern_num = config['AO']['control_coeff_num'] - 2
            self.strehl = np.zeros([2, self.zern_num])
            self.loop_rms_zern, self.loop_rms_zern_part = (np.zeros([self.AO_settings['loop_max'] + 1, self.zern_num]) for i in range(2))
            self.loop_num = np.zeros(self.zern_num)

            self.message.emit('\nProcess started for closed-loop AO via slopes...')

            prev1 = time.perf_counter()

            for n in range(config['zern_test']['run_num']):

                # Run closed-loop control for each zernike mode aberration
                for k in range(config['zern_test']['incre_num']):

                    # Initialise AO information parameter
                    self.AO_info = {'zern_test': {}}

                    # Create new datasets in HDF5 file to store closed-loop AO data and open file
                    get_dset(self.SB_settings, 'zern_test', flag = 0)
                    data_file = h5py.File('data_info.h5', 'a')
                    data_set_1 = data_file['AO_img']['zern_test']
                    data_set_2 = data_file['AO_info']['zern_test']

                    # Initialise array to store initial and final detected value of each generated Zernike mode
                    self.det_cor_zern_coeff = np.zeros([self.zern_num * 2, self.zern_num])

                    # Determine the amplitude to be generated for each Zernike mode
                    zern_amp_gen = config['zern_test']['incre_amp'] * (k + 1)

                    # Determine initial loop gain for generation of each Zernike mode
                    if zern_amp_gen <= 0.1:
                        loop_gain_gen = 0.1
                    elif zern_amp_gen > 0.1 and zern_amp_gen <= 0.2:
                        loop_gain_gen = 0.2
                    elif zern_amp_gen > 0.2:
                        loop_gain_gen = 0.3

                    for j in range(self.zern_num):

                        print('On amplitude {} Zernike mode {}'.format(k + 1, j + 3))

                        for i in range(self.AO_settings['loop_max'] + 1):
                            
                            if self.loop:
                                
                                try:

                                    # Update mirror control voltages
                                    if i == 0:

                                        if not config['dummy']:

                                            # Generate one Zernike mode on DM for correction each time
                                            self.zern_coeff[j + 2, 0] = zern_amp_gen

                                            # Run closed-loop to generate a precise amount of Zernike modes using DM
                                            for m in range(config['zern_test']['loop_max_gen']):

                                                if m == 0:

                                                    voltages[:] = config['DM']['vol_bias']

                                                else:

                                                    # Use smaller loop gain towards latter half of iteration
                                                    if m >= config['zern_test']['loop_max_gen'] // 2:
                                                        loop_gain_gen = 0.1

                                                    # Update control voltages
                                                    voltages -= loop_gain_gen * np.ravel(np.dot(self.mirror_settings['control_matrix_zern']\
                                                        [:,:config['AO']['control_coeff_num']], (zern_array_det[:config['AO']['control_coeff_num']] - self.zern_coeff)))

                                                # Send values vector to mirror
                                                self.mirror.Send(voltages)

                                                # Wait for DM to settle
                                                time.sleep(config['DM']['settling_time'])
                                            
                                                # Acquire S-H spots using camera
                                                AO_image_stack = acq_image(self.sensor, self.SB_settings['sensor_height'], self.SB_settings['sensor_width'], acq_mode = 1)
                                                AO_image = np.mean(AO_image_stack, axis = 2)

                                                # Image thresholding to remove background
                                                AO_image = AO_image - config['image']['threshold'] * np.amax(AO_image)
                                                AO_image[AO_image < 0] = 0
                                                self.image.emit(AO_image)

                                                # Append image to list
                                                dset_append(data_set_1, 'real_AO_img', AO_image)

                                                # Calculate centroids of S-H spots
                                                act_cent_coord, act_cent_coord_x, act_cent_coord_y, slope_x, slope_y = acq_centroid(self.SB_settings, flag = 2)
                                                act_cent_coord, act_cent_coord_x, act_cent_coord_y = map(np.asarray, [act_cent_coord, act_cent_coord_x, act_cent_coord_y])

                                                # Draw actual S-H spot centroids on image layer
                                                AO_image.ravel()[act_cent_coord.astype(int)] = 0
                                                self.image.emit(AO_image)

                                                # Take tip\tilt off
                                                slope_x -= np.mean(slope_x)
                                                slope_y -= np.mean(slope_y)

                                                # Concatenate slopes into one slope matrix
                                                slope = (np.concatenate((slope_x, slope_y), axis = 1)).T

                                                # Get detected zernike coefficients from slope matrix
                                                zern_array_det = np.dot(self.mirror_settings['conv_matrix'], slope)

                                                # print('Detected amplitude of mode {} is {} um'.format(m + 3, zern_array_det[m + 2]))
                                        else:

                                            voltages[:] = config['DM']['vol_bias']
                                    else:

                                        voltages -= config['AO']['loop_gain'] * np.ravel(np.dot(self.mirror_settings['control_matrix_slopes'], slope_err))

                                        print('Max and min values of voltages {} are: {}, {}'.format(i, np.max(voltages), np.min(voltages)))

                                    if config['dummy']:

                                        # Update phase profile and retrieve S-H spot image 
                                        if i == 0:

                                            # Option 1: Generate real zernike phase profile using DM control matrix
                                            if config['real_zernike']:

                                                # Generate input zernike coefficient array
                                                self.zern_coeff[j + 2] = config['zern_test']['incre_amp'] * (k + 1)

                                                # Retrieve actuator voltages from zernike coefficient array
                                                voltages = np.ravel(np.dot(self.mirror_settings['control_matrix_zern']\
                                                    [:,:config['AO']['control_coeff_num']], self.zern_coeff))
                                                
                                                # Generate zernike phase profile from DM
                                                phase_init = self.phase_calc(voltages)

                                                # Check whether need to incorporate sample reflectance process
                                                if config['reflect_on'] == 1:
                                                    phase_init = reflect_process(self.SB_settings, phase_init, self.pupil_diam)
                                                
                                            # Option 2: Generate ideal zernike phase profile
                                            else:
                                                
                                                # Generate input zernike coefficient array
                                                self.zern_coeff[j + 2] = config['zern_test']['incre_amp'] * (k + 1)
                                                
                                                # Generate ideal zernike phase profile
                                                phase_init = zern_phase(self.SB_settings,  self.zern_coeff)

                                                # Check whether need to incorporate sample reflectance process
                                                if config['reflect_on'] == 1:
                                                    phase_init = reflect_process(self.SB_settings, phase_init, self.pupil_diam)

                                            # Display initial phase
                                            self.image.emit(phase_init)

                                            # print('\nMax and min values of phase {} are: {} um, {} um'.format(i, np.amax(phase_init), np.amin(phase_init)))

                                            # Get simulated S-H spots and append to list
                                            AO_image, spot_cent_x, spot_cent_y = fft_spot_from_phase(self.SB_settings, phase_init)
                                            dset_append(data_set_1, 'dummy_AO_img', AO_image)
                                            dset_append(data_set_1, 'dummy_spot_cent_x', spot_cent_x)
                                            dset_append(data_set_1, 'dummy_spot_cent_y', spot_cent_y)
                                            
                                            phase = phase_init.copy()

                                        else:

                                            # Calculate phase profile introduced by DM
                                            delta_phase = self.phase_calc(voltages)

                                            # Update phase data
                                            phase = phase_init - delta_phase

                                            # Display corrected phase
                                            self.image.emit(phase)

                                            # print('Max and min values of phase {} are: {} um, {} um'.format(i, np.amax(phase), np.amin(phase)))

                                            # Get simulated S-H spots and append to list
                                            AO_image, spot_cent_x, spot_cent_y = fft_spot_from_phase(self.SB_settings, phase)
                                            dset_append(data_set_1, 'dummy_AO_img', AO_image)
                                            dset_append(data_set_1, 'dummy_spot_cent_x', spot_cent_x)
                                            dset_append(data_set_1, 'dummy_spot_cent_y', spot_cent_y)

                                    else:

                                        # Send values vector to mirror
                                        self.mirror.Send(voltages)
                                        
                                        # Wait for DM to settle
                                        time.sleep(config['DM']['settling_time'])
                                    
                                        # Acquire S-H spots using camera
                                        AO_image_stack = acq_image(self.sensor, self.SB_settings['sensor_height'], self.SB_settings['sensor_width'], acq_mode = 1)
                                        AO_image = np.mean(AO_image_stack, axis = 2)

                                    # Image thresholding to remove background
                                    AO_image = AO_image - config['image']['threshold'] * np.amax(AO_image)
                                    AO_image[AO_image < 0] = 0
                                    self.image.emit(AO_image)

                                    # Append image to list
                                    dset_append(data_set_1, 'real_AO_img', AO_image)

                                    # Calculate centroids of S-H spots
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y, slope_x, slope_y = acq_centroid(self.SB_settings, flag = 2) 
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y = map(np.asarray, [act_cent_coord, act_cent_coord_x, act_cent_coord_y])

                                    # Draw actual S-H spot centroids on image layer
                                    AO_image.ravel()[act_cent_coord.astype(int)] = 0
                                    self.image.emit(AO_image)

                                    # Take tip\tilt off
                                    slope_x -= np.mean(slope_x)
                                    slope_y -= np.mean(slope_y)

                                    # Concatenate slopes into one slope matrix
                                    slope = (np.concatenate((slope_x, slope_y), axis = 1)).T

                                    # Get phase residual (slope residual error) and calculate root mean square (rms) error
                                    slope_err = slope.copy()

                                    # Get detected zernike coefficients from slope matrix
                                    self.zern_coeff_detect = np.dot(self.mirror_settings['conv_matrix'], slope)

                                    if i == 0:
                                        self.det_cor_zern_coeff[2 * j, :] = self.zern_coeff_detect[2:config['AO']['control_coeff_num'], 0].T

                                    # Get phase residual (zernike coefficient residual error) and calculate root mean square (rms) error
                                    zern_err = self.zern_coeff_detect.copy()
                                    zern_err_part = self.zern_coeff_detect.copy()
                                    zern_err_part[[0, 1], 0] = 0
                                    rms_zern = np.sqrt((zern_err ** 2).sum())
                                    rms_zern_part = np.sqrt((zern_err_part ** 2).sum())
                                    self.loop_rms_zern[i,j] = rms_zern
                                    self.loop_rms_zern_part[i,j] = rms_zern_part

                                    strehl = np.exp(-(2 * np.pi / config['AO']['lambda'] * rms_zern_part) ** 2)
                                    if config['dummy']:
                                        strehl_2 = self.strehl_calc(phase)

                                    print('Full zernike root mean square error {} is {} um'.format(i, rms_zern))                      

                                    # Append data to list
                                    if config['dummy']:
                                        dset_append(data_set_2, 'dummy_spot_slope_x', slope_x)
                                        dset_append(data_set_2, 'dummy_spot_slope_y', slope_y)
                                        dset_append(data_set_2, 'dummy_spot_slope', slope)
                                        dset_append(data_set_2, 'dummy_spot_zern_err', zern_err)
                                    else:
                                        dset_append(data_set_2, 'real_spot_slope_x', slope_x)
                                        dset_append(data_set_2, 'real_spot_slope_y', slope_y)
                                        dset_append(data_set_2, 'real_spot_slope', slope)
                                        dset_append(data_set_2, 'real_spot_zern_err', zern_err)

                                    # Compare rms error with tolerance factor (Marechel criterion) and decide whether to break from loop
                                    if strehl >= config['AO']['tolerance_fact_strehl'] or i == self.AO_settings['loop_max']:
                                        self.strehl[0,j] = strehl
                                        if config['dummy']:
                                            self.strehl[1,j] = strehl_2
                                        self.loop_num[j] = i
                                        self.zern_coeff[j + 2] = 0
                                        self.det_cor_zern_coeff[2 * j + 1, :] = self.zern_coeff_detect[2:config['AO']['control_coeff_num'], 0].T
                                        break                 

                                except Exception as e:
                                    print(e)
                            else:

                                self.done.emit()

                    # Save detected aberrations to file
                    sp.io.savemat('zern_gen_det_cor/slope_correction/amp_' + str(config['zern_test']['incre_amp'] * (k + 1)) + 'run_' + str(n) + '.mat',\
                        dict(slope_det_cor_zern_amp = self.det_cor_zern_coeff))

                    # Close HDF5 file
                    data_file.close()

            self.message.emit('\nProcess complete.')

            prev2 = time.perf_counter()
            print('Time for closed-loop AO process is:', (prev2 - prev1))

            """
            Returns closed-loop AO information into self.AO_info
            """             
            if self.log:
                
                self.AO_info['zern_test']['loop_num'] = self.loop_num
                self.AO_info['zern_test']['residual_phase_err_zern'] = self.loop_rms_zern
                self.AO_info['zern_test']['residual_phase_err_zern_part'] = self.loop_rms_zern_part
                self.AO_info['zern_test']['strehl_ratio'] = self.strehl

                self.info.emit(self.AO_info)
                self.write.emit()
            else:

                self.done.emit()

            # Finished closed-loop AO process
            self.done.emit()

        except Exception as e:
            raise
            self.error.emit(e)

    @Slot(object)
    def run2(self):
        try:
            # Set process flags
            self.loop = True 
            self.log = True

            # Start thread
            self.start.emit()

            """
            Perform a number of line scans across specimen and retrieve Zernike coefficients from each scan point
            """
            self.message.emit('\nProcess started for Zernike coefficient retrieval from different points along line scan...')

            prev1 = time.perf_counter()

            # Scan multiple points across a line in both x and y directions for a given number of loops over a large FOV (600um)
            if config['zern_test']['large_flag']:

                # Initialise array for storing retrieved zernike coefficients
                self.zern_x = np.zeros([config['AO']['recon_coeff_num'], config['zern_test']['scan_num_x_large'], config['zern_test']['loop_num']])
                self.zern_y = np.zeros([config['AO']['recon_coeff_num'], config['zern_test']['scan_num_y_large'], config['zern_test']['loop_num']])

                # Initialise array for storing retrieved slope values
                self.slope_x = np.zeros([self.SB_settings['act_ref_cent_num'] * 2, config['zern_test']['scan_num_x_large'], config['zern_test']['loop_num']])
                self.slope_y = np.zeros([self.SB_settings['act_ref_cent_num'] * 2, config['zern_test']['scan_num_y_large'], config['zern_test']['loop_num']])

                # Generate relevant amounts of x/y scan voltages (normalised) for scanning across sample
                x_array_large = np.linspace(-config['zern_test']['x_amp_large'], config['zern_test']['x_amp_large'], config['zern_test']['scan_num_x_large'])
                y_array_large = np.linspace(-config['zern_test']['y_amp_large'], config['zern_test']['y_amp_large'], config['zern_test']['scan_num_y_large'])

                for n in range(config['zern_test']['run_num']):

                    # Initialise AO information parameter
                    self.AO_info = {'zern_test': {}}

                    # Create new datasets in HDF5 file to store closed-loop AO data and open file
                    get_dset(self.SB_settings, 'zern_test', flag = 0)
                    data_file = h5py.File('data_info.h5', 'a')
                    data_set_1 = data_file['AO_img']['zern_test']
                    data_set_2 = data_file['AO_info']['zern_test']
                    
                    # Reset deformable mirror
                    self.mirror.Reset()

                    for l in range(config['zern_test']['loop_num']):

                        # Reset scanner
                        self.scanner.ResetDevicePosition()

                        print('Large FOV run {} x-scan loop {}'.format(n + 1, l + 1))

                        for m in range(config['zern_test']['scan_num_x_large']):
                        
                            if self.loop:
                                
                                try:
                                    
                                    # Send voltages to scanner
                                    self.scanner.GoToDevicePosition(x_array_large[m], 0, 255, 10)
                                
                                    # Acquire S-H spots using camera
                                    AO_image_stack = acq_image(self.sensor, self.SB_settings['sensor_height'], self.SB_settings['sensor_width'], acq_mode = 1)
                                    AO_image = np.mean(AO_image_stack, axis = 2)

                                    # Image thresholding to remove background
                                    AO_image = AO_image - config['image']['threshold'] * np.amax(AO_image)
                                    AO_image[AO_image < 0] = 0
                                    self.image.emit(AO_image)

                                    # Append image to list
                                    dset_append(data_set_1, 'real_AO_img', AO_image)

                                    # Calculate centroids of S-H spots
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y, slope_x, slope_y = acq_centroid(self.SB_settings, flag = 2) 
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y = map(np.asarray, [act_cent_coord, act_cent_coord_x, act_cent_coord_y])

                                    # Draw actual S-H spot centroids on image layer
                                    AO_image.ravel()[act_cent_coord.astype(int)] = 0
                                    self.image.emit(AO_image)

                                    # Take tip\tilt off
                                    slope_x -= np.mean(slope_x)
                                    slope_y -= np.mean(slope_y)

                                    # Concatenate slopes into one slope matrix
                                    slope = (np.concatenate((slope_x, slope_y), axis = 1)).T
                                    self.slope_x[:,m,l] = slope[:,0]

                                    # Get detected zernike coefficients from slope matrix
                                    self.zern_coeff_detect = np.dot(self.mirror_settings['conv_matrix'], slope)

                                    # Get phase residual (zernike coefficient residual error) and calculate root mean square (rms) error
                                    zern_err = self.zern_coeff_detect.copy()
                                    rms_zern = np.sqrt((zern_err ** 2).sum())
                                    self.zern_x[:,m,l] = zern_err[:,0]

                                    print('Full zernike root mean square error {} is {} um'.format(m + 1, rms_zern))                              

                                except Exception as e:
                                    print(e)
                            else:

                                self.done.emit()

                    for l in range(config['zern_test']['loop_num']):

                        # Reset scanner
                        self.scanner.ResetDevicePosition()

                        print('Large FOV run {} y-scan loop {}'.format(n + 1, l + 1))

                        for m in range(config['zern_test']['scan_num_y_large']):
                        
                            if self.loop:
                                
                                try:

                                    # Send voltages to scanner
                                    self.scanner.GoToDevicePosition(0, y_array_large[m], 255, 10)
                                
                                    # Acquire S-H spots using camera
                                    AO_image_stack = acq_image(self.sensor, self.SB_settings['sensor_height'], self.SB_settings['sensor_width'], acq_mode = 1)
                                    AO_image = np.mean(AO_image_stack, axis = 2)

                                    # Image thresholding to remove background
                                    AO_image = AO_image - config['image']['threshold'] * np.amax(AO_image)
                                    AO_image[AO_image < 0] = 0
                                    self.image.emit(AO_image)

                                    # Append image to list
                                    dset_append(data_set_1, 'real_AO_img', AO_image)

                                    # Calculate centroids of S-H spots
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y, slope_x, slope_y = acq_centroid(self.SB_settings, flag = 2) 
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y = map(np.asarray, [act_cent_coord, act_cent_coord_x, act_cent_coord_y])

                                    # Draw actual S-H spot centroids on image layer
                                    AO_image.ravel()[act_cent_coord.astype(int)] = 0
                                    self.image.emit(AO_image)

                                    # Take tip\tilt off
                                    slope_x -= np.mean(slope_x)
                                    slope_y -= np.mean(slope_y)

                                    # Concatenate slopes into one slope matrix
                                    slope = (np.concatenate((slope_x, slope_y), axis = 1)).T
                                    self.slope_y[:,m,l] = slope[:,0]

                                    # Get detected zernike coefficients from slope matrix
                                    self.zern_coeff_detect = np.dot(self.mirror_settings['conv_matrix'], slope)

                                    # Get phase residual (zernike coefficient residual error) and calculate root mean square (rms) error
                                    zern_err = self.zern_coeff_detect.copy()
                                    rms_zern = np.sqrt((zern_err ** 2).sum())
                                    self.zern_y[:,m,l] = zern_err[:,0]

                                    print('Full zernike root mean square error {} is {} um'.format(m + 1, rms_zern))                              

                                except Exception as e:
                                    print(e)
                            else:

                                self.done.emit()

                    sp.io.savemat('xy_scan_aberr_meas/600um/x_scan_zern_coeff_' + str(n) + '.mat', dict(x_scan_zern_coeff = self.zern_x))
                    sp.io.savemat('xy_scan_aberr_meas/600um/y_scan_zern_coeff_' + str(n) + '.mat', dict(y_scan_zern_coeff = self.zern_y))
                    sp.io.savemat('xy_scan_aberr_meas/600um/x_scan_slope_val_' + str(n) + '.mat', dict(x_scan_slope_val = self.slope_x))
                    sp.io.savemat('xy_scan_aberr_meas/600um/y_scan_slope_val_' + str(n) + '.mat', dict(y_scan_slope_val = self.slope_y))

                    # Close HDF5 file
                    data_file.close()

            # Scan multiple points across a line in both x and y directions for a given number of loops over a small FOV (100um)
            if config['zern_test']['small_flag']:

                # Initialise array for storing retrieved zernike coefficients
                self.zern_x = np.zeros([config['AO']['recon_coeff_num'], config['zern_test']['scan_num_x_small'], config['zern_test']['loop_num']])
                self.zern_y = np.zeros([config['AO']['recon_coeff_num'], config['zern_test']['scan_num_y_small'], config['zern_test']['loop_num']])

                # Initialise array for storing retrieved slope values
                self.slope_x = np.zeros([self.SB_settings['act_ref_cent_num'] * 2, config['zern_test']['scan_num_x_small'], config['zern_test']['loop_num']])
                self.slope_y = np.zeros([self.SB_settings['act_ref_cent_num'] * 2, config['zern_test']['scan_num_y_small'], config['zern_test']['loop_num']])

                # Generate relevant amounts of x/y scan voltages (normalised) for scanning across sample
                x_array_small = np.linspace(-config['zern_test']['x_amp_small'], config['zern_test']['x_amp_small'], config['zern_test']['scan_num_x_small'])
                y_array_small = np.linspace(-config['zern_test']['y_amp_small'], config['zern_test']['y_amp_small'], config['zern_test']['scan_num_y_small'])

                for n in range(config['zern_test']['run_num']):

                    # Initialise AO information parameter
                    self.AO_info = {'zern_test': {}}

                    # Create new datasets in HDF5 file to store closed-loop AO data and open file
                    get_dset(self.SB_settings, 'zern_test', flag = 0)
                    data_file = h5py.File('data_info.h5', 'a')
                    data_set_1 = data_file['AO_img']['zern_test']
                    data_set_2 = data_file['AO_info']['zern_test']
                    
                    # Reset deformable mirror
                    self.mirror.Reset()

                    for l in range(config['zern_test']['loop_num']):

                        # Reset scanner
                        self.scanner.ResetDevicePosition()

                        print('Small FOV run {} x-scan loop {}'.format(n + 1, l + 1))

                        for m in range(config['zern_test']['scan_num_x_small']):
                        
                            if self.loop:
                                
                                try:
                                    
                                    # Send voltages to scanner
                                    self.scanner.GoToDevicePosition(x_array_small[m], 0, 255, 10)
                                
                                    # Acquire S-H spots using camera
                                    AO_image_stack = acq_image(self.sensor, self.SB_settings['sensor_height'], self.SB_settings['sensor_width'], acq_mode = 1)
                                    AO_image = np.mean(AO_image_stack, axis = 2)

                                    # Image thresholding to remove background
                                    AO_image = AO_image - config['image']['threshold'] * np.amax(AO_image)
                                    AO_image[AO_image < 0] = 0
                                    self.image.emit(AO_image)

                                    # Append image to list
                                    dset_append(data_set_1, 'real_AO_img', AO_image)

                                    # Calculate centroids of S-H spots
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y, slope_x, slope_y = acq_centroid(self.SB_settings, flag = 2) 
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y = map(np.asarray, [act_cent_coord, act_cent_coord_x, act_cent_coord_y])

                                    # Draw actual S-H spot centroids on image layer
                                    AO_image.ravel()[act_cent_coord.astype(int)] = 0
                                    self.image.emit(AO_image)

                                    # Take tip\tilt off
                                    slope_x -= np.mean(slope_x)
                                    slope_y -= np.mean(slope_y)

                                    # Concatenate slopes into one slope matrix
                                    slope = (np.concatenate((slope_x, slope_y), axis = 1)).T
                                    self.slope_x[:,m,l] = slope[:,0]

                                    # Get detected zernike coefficients from slope matrix
                                    self.zern_coeff_detect = np.dot(self.mirror_settings['conv_matrix'], slope)

                                    # Get phase residual (zernike coefficient residual error) and calculate root mean square (rms) error
                                    zern_err = self.zern_coeff_detect.copy()
                                    rms_zern = np.sqrt((zern_err ** 2).sum())
                                    self.zern_x[:,m,l] = zern_err[:,0]

                                    print('Full zernike root mean square error {} is {} um'.format(m + 1, rms_zern))                              

                                except Exception as e:
                                    print(e)
                            else:

                                self.done.emit()

                    for l in range(config['zern_test']['loop_num']):

                        # Reset scanner
                        self.scanner.ResetDevicePosition()

                        print('Small FOV run {} y-scan loop {}'.format(n + 1, l + 1))

                        for m in range(config['zern_test']['scan_num_y_small']):
                        
                            if self.loop:
                                
                                try:

                                    # Send voltages to scanner
                                    self.scanner.GoToDevicePosition(0, y_array_small[m], 255, 10)
                                
                                    # Acquire S-H spots using camera
                                    AO_image_stack = acq_image(self.sensor, self.SB_settings['sensor_height'], self.SB_settings['sensor_width'], acq_mode = 1)
                                    AO_image = np.mean(AO_image_stack, axis = 2)

                                    # Image thresholding to remove background
                                    AO_image = AO_image - config['image']['threshold'] * np.amax(AO_image)
                                    AO_image[AO_image < 0] = 0
                                    self.image.emit(AO_image)

                                    # Append image to list
                                    dset_append(data_set_1, 'real_AO_img', AO_image)

                                    # Calculate centroids of S-H spots
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y, slope_x, slope_y = acq_centroid(self.SB_settings, flag = 2) 
                                    act_cent_coord, act_cent_coord_x, act_cent_coord_y = map(np.asarray, [act_cent_coord, act_cent_coord_x, act_cent_coord_y])

                                    # Draw actual S-H spot centroids on image layer
                                    AO_image.ravel()[act_cent_coord.astype(int)] = 0
                                    self.image.emit(AO_image)

                                    # Take tip\tilt off
                                    slope_x -= np.mean(slope_x)
                                    slope_y -= np.mean(slope_y)

                                    # Concatenate slopes into one slope matrix
                                    slope = (np.concatenate((slope_x, slope_y), axis = 1)).T
                                    self.slope_y[:,m,l] = slope[:,0]

                                    # Get detected zernike coefficients from slope matrix
                                    self.zern_coeff_detect = np.dot(self.mirror_settings['conv_matrix'], slope)

                                    # Get phase residual (zernike coefficient residual error) and calculate root mean square (rms) error
                                    zern_err = self.zern_coeff_detect.copy()
                                    rms_zern = np.sqrt((zern_err ** 2).sum())
                                    self.zern_y[:,m,l] = zern_err[:,0]

                                    print('Full zernike root mean square error {} is {} um'.format(m + 1, rms_zern))                              

                                except Exception as e:
                                    print(e)
                            else:

                                self.done.emit()

                    sp.io.savemat('xy_scan_aberr_meas/100um/x_scan_zern_coeff_' + str(n) + '.mat', dict(x_scan_zern_coeff = self.zern_x))
                    sp.io.savemat('xy_scan_aberr_meas/100um/y_scan_zern_coeff_' + str(n) + '.mat', dict(y_scan_zern_coeff = self.zern_y))
                    sp.io.savemat('xy_scan_aberr_meas/100um/x_scan_slope_val_' + str(n) + '.mat', dict(x_scan_slope_val = self.slope_x))
                    sp.io.savemat('xy_scan_aberr_meas/100um/y_scan_slope_val_' + str(n) + '.mat', dict(y_scan_slope_val = self.slope_y))

                    # Close HDF5 file
                    data_file.close()

            self.message.emit('\nProcess complete.')

            prev2 = time.perf_counter()
            print('Time for Zernike coefficient retrieval from different points along line scan is:', (prev2 - prev1))

            """
            Returns closed-loop AO information into self.AO_info
            """             
            if self.log:

                self.AO_info['zern_test']['x_scan_zern_coeff'] = self.zern_x
                self.AO_info['zern_test']['y_scan_zern_coeff'] = self.zern_y
                self.AO_info['zern_test']['x_scan_slope_val'] = self.slope_x
                self.AO_info['zern_test']['y_scan_slope_val'] = self.slope_y

                self.info.emit(self.AO_info)
                self.write.emit()

            else:

                self.done.emit()

            # Finished closed-loop AO process
            self.done.emit()

        except Exception as e:
            raise
            self.error.emit(e)

    @Slot(object)
    def stop(self):
        self.loop = False
        self.log = False


