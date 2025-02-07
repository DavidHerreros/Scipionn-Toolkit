#!/usr/bin/env python
# **************************************************************************
# *
# * Authors:  David Herreros Calero (dherreros@cnb.csic.es)
# *
# * Unidad de  Bioinformatica of Centro Nacional de Biotecnologia , CSIC
# *
# * This program is free software; you can redistribute it and/or modify
# * it under the terms of the GNU General Public License as published by
# * the Free Software Foundation; either version 2 of the License, or
# * (at your option) any later version.
# *
# * This program is distributed in the hope that it will be useful,
# * but WITHOUT ANY WARRANTY; without even the implied warranty of
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# * GNU General Public License for more details.
# *
# * You should have received a copy of the GNU General Public License
# * along with this program; if not, write to the Free Software
# * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA
# * 02111-1307  USA
# *
# *  All comments concerning this program package may be sent to the
# *  e-mail address 'scipion@cnb.csic.es'
# *
# **************************************************************************


import os
import numpy as np

import tensorflow as tf

from deprecated.generator_deep_pose import Generator
from deprecated.deep_pose import AutoEncoder
from tensorflow_toolkit.utils import xmippEulerFromMatrix


# from tensorflow_toolkit.datasets.dataset_template import sequence_to_data_pipeline, create_dataset


# # os.environ["CUDA_VISIBLE_DEVICES"]="0,2,3,4"
# physical_devices = tf.config.list_physical_devices('GPU')
# for gpu_instance in physical_devices:
#     tf.config.experimental.set_memory_growth(gpu_instance, True)


def predict(md_file, weigths_file, architecture, ctfType, pad=2, sr=1.0, applyCTF=1):
    # Create data generator
    generator = Generator(md_file=md_file, shuffle=False, batch_size=16,
                          step=1, splitTrain=1.0,
                          pad_factor=pad, sr=sr, applyCTF=applyCTF)

    # Tensorflow data pipeline
    # generator_dataset, generator = sequence_to_data_pipeline(generator)
    # dataset = create_dataset(generator_dataset, generator, shuffle=False, batch_size=16)

    # Load model
    autoencoder = AutoEncoder(generator, architecture=architecture, CTF=ctfType)
    autoencoder.build(input_shape=(None, generator.xsize, generator.xsize, 1))
    autoencoder.load_weights(weigths_file)

    # Get poses
    # alignment = []
    print("------------------ Predicting particles... ------------------")
    alignment, shifts = autoencoder.predict(generator)

    # pred_algn, pred_shifts, loss = autoencoder.predict(generator)
    #
    # # Get only best predictions according to symmetric loss
    # index_min = np.argmin(loss, axis=1)
    # pred_algn = pred_algn[np.arange(pred_algn.shape[0]), ..., index_min]
    # pred_shifts = pred_algn[np.arange(pred_shifts.shape[0]), ..., index_min]
    #
    # # Save results depending on training mode
    # if refinePose:
    #     alignment = pred_algn
    # else:
    #     for matrix in pred_algn:
    #         alignment.append(xmippEulerFromMatrix(matrix))
    # shifts = pred_shifts

    # Save space to metadata file
    # alignment = np.vstack(alignment)
    euler_angles = np.zeros((alignment.shape[0], 3))
    idx = 0
    for matrix in alignment:
        euler_angles[idx] = xmippEulerFromMatrix(matrix)
        idx += 1

    shifts = np.vstack(shifts)

    generator.metadata[:, 'delta_angle_rot'] = euler_angles[:, 0]
    generator.metadata[:, 'delta_angle_tilt'] = euler_angles[:, 1]
    generator.metadata[:, 'delta_angle_psi'] = euler_angles[:, 2]
    generator.metadata[:, 'delta_shift_x'] = shifts[:, 0]
    generator.metadata[:, 'delta_shift_y'] = shifts[:, 1]

    generator.metadata.write(md_file, overwrite=True)


def main():
    import argparse

    # Input parameters
    parser = argparse.ArgumentParser()
    parser.add_argument('--md_file', type=str, required=True)
    parser.add_argument('--weigths_file', type=str, required=True)
    parser.add_argument('--architecture', type=str, required=True)
    parser.add_argument('--ctf_type', type=str, required=True)
    parser.add_argument('--pad', type=int, required=False, default=2)
    parser.add_argument('--sr', type=float, required=True)
    parser.add_argument('--apply_ctf', type=int, required=True)
    parser.add_argument('--gpu', type=str)

    args = parser.parse_args()

    if args.gpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu
    physical_devices = tf.config.list_physical_devices('GPU')
    for gpu_instance in physical_devices:
        tf.config.experimental.set_memory_growth(gpu_instance, True)

    inputs = {"md_file": args.md_file, "weigths_file": args.weigths_file,
              "architecture": args.architecture,"ctfType": args.ctf_type, "pad": args.pad,
              "sr": args.sr, "applyCTF": args.apply_ctf}

    # Initialize volume slicer
    predict(**inputs)
