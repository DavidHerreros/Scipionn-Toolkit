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


from packaging import version
import numpy as np

import tensorflow as tf
from tensorflow.keras import layers
tf_version = tf.__version__
allow_open3d = version.parse(tf_version) >= version.parse("2.15.0")

if allow_open3d:
    import open3d.ml.tf as ml3d

from tensorflow_toolkit.utils import computeCTF, euler_matrix_batch
from tensorflow_toolkit.layers.residue_conv2d import ResidueConv2D


def lennard_jones(r2, radius):
    # r2 = r * radius * radius
    r6 = r2 * r2 * r2
    r12 = r6 * r6
    s6 = 0.1176
    s12 = 0.0138
    return (s12 / r12) - (s6 / r6)

def simple_clash(r2, splits, radius):
    lengths = tf.math.subtract(splits[1:], splits[:-1])
    expanded_lengths = tf.cast(tf.repeat(lengths, lengths), tf.float32)
    # r = tf.sqrt(r2)
    radius2 = radius * radius
    # r2 = r * radius * radius
    return tf.abs(r2 - radius2) / expanded_lengths


class Encoder(tf.keras.Model):
    def __init__(self, latent_dim, input_dim, refinePose, architecture="convnn",
                 mode="spa", jit_compile=True):
        super(Encoder, self).__init__()
        self.latent_dim = latent_dim
        l2 = tf.keras.regularizers.l2(1e-3)
        # shift_activation = lambda y: 2 * tf.keras.activations.tanh(y)

        # XLA compilation of methods
        self.call = tf.function(jit_compile=jit_compile)(self.call)

        encoder_inputs = tf.keras.Input(shape=(input_dim, input_dim, 1))
        subtomo_pe = tf.keras.Input(shape=(100,))

        x = tf.keras.layers.Flatten()(encoder_inputs)

        if architecture == "mlpnn":
            for _ in range(12):
                x = layers.Dense(1024, activation='relu', kernel_regularizer=l2)(x)
            x = layers.Dropout(0.3)(x)
            x = layers.BatchNormalization()(x)

        elif architecture == "convnn":
            for _ in range(3):
                x = layers.Dense(64 * 64, activation='relu', kernel_regularizer=l2)(x)

            x = tf.keras.layers.Dense(64 * 64, kernel_regularizer=l2)(x)
            x = tf.keras.layers.Reshape((64, 64, 1))(x)

            x = tf.keras.layers.Conv2D(64, 5, activation="relu", strides=(2, 2), padding="same")(x)
            for _ in range(1):
                x = ResidueConv2D(64, 5, activation="relu", padding="same")(x)
            x = tf.keras.layers.Conv2D(32, 5, activation="relu", strides=(2, 2), padding="same")(x)
            for _ in range(1):
                x = ResidueConv2D(32, 5, activation="relu", padding="same")(x)
            x = tf.keras.layers.Conv2D(16, 3, activation="relu", strides=(2, 2), padding="same")(x)
            x = ResidueConv2D(16, 3, activation="relu", padding="same")(x)
            x = tf.keras.layers.Flatten()(x)
            x = tf.keras.layers.Dropout(.1)(x)
            x = tf.keras.layers.BatchNormalization()(x)

            for _ in range(3):
                x = layers.Dense(3 * 16 * 16, activation='relu', kernel_regularizer=l2)(x)
            x = layers.Dropout(.1)(x)
            x = layers.BatchNormalization()(x)

            # x = tf.keras.layers.Conv2D(4, 5, activation="relu", strides=(2, 2), padding="same")(x)
            # x = tf.keras.layers.Conv2D(8, 5, activation="relu", strides=(2, 2), padding="same")(x)
            # x = tf.keras.layers.Conv2D(16, 3, activation="relu", strides=(2, 2), padding="same")(x)
            # x = tf.keras.layers.Conv2D(16, 3, activation="relu", strides=(2, 2), padding="same")(x)
            # x = tf.keras.layers.Flatten()(x)
            # x = tf.keras.layers.Dropout(.1)(x)
            # x = tf.keras.layers.BatchNormalization()(x)

        if mode == "spa":
            z_space_x = layers.Dense(latent_dim, activation="linear", name="z_space_x")(x)
            z_space_y = layers.Dense(latent_dim, activation="linear", name="z_space_y")(x)
            z_space_z = layers.Dense(latent_dim, activation="linear", name="z_space_z")(x)
        elif mode == "tomo":
            latent = layers.Dense(1024, activation="relu")(subtomo_pe)
            for _ in range(2):  # TODO: Is it better to use 12 hidden layers as in Zernike3Deep?
                latent = layers.Dense(1024, activation="relu")(latent)
            z_space_x = layers.Dense(latent_dim, activation="linear", name="z_space_x")(latent)
            z_space_y = layers.Dense(latent_dim, activation="linear", name="z_space_y")(latent)
            z_space_z = layers.Dense(latent_dim, activation="linear", name="z_space_z")(latent)

        delta_euler = layers.Dense(3, activation="linear", name="delta_euler", trainable=refinePose)(x)

        # delta_shifts = layers.Dense(2, activation=shift_activation, name="delta_shifts")(x)
        delta_shifts = layers.Dense(2, activation="linear", name="delta_shifts", trainable=refinePose)(x)

        if mode == "spa":
            self.encoder = tf.keras.Model(encoder_inputs,
                                          [z_space_x, z_space_y, z_space_z, delta_euler, delta_shifts], name="encoder")
        elif mode == "tomo":
            self.encoder = tf.keras.Model([encoder_inputs, subtomo_pe],
                                          [z_space_x, z_space_y, z_space_z, delta_euler, delta_shifts], name="encoder")
            self.encoder_latent = tf.keras.Model(subtomo_pe, [z_space_x, z_space_y, z_space_z], name="encode_latent")

    # @tf.function(jit_compile=True)
    def call(self, x):
        return self.encoder(x)


class Decoder:
    # @tf.function(jit_compile=True)
    def __init__(self, generator, CTF="apply", jit_compile=True):
        super(Decoder, self).__init__()
        self.generator = generator
        self.CTF = CTF

        # XLA compilation of methods
        self.prepare_batch = tf.function(jit_compile=jit_compile)(self.prepare_batch)
        self.compute_field_volume = tf.function(jit_compile=jit_compile)(self.compute_field_volume)
        self.compute_field_atoms = tf.function(jit_compile=jit_compile)(self.compute_field_atoms)
        self.compute_atom_cost_params = tf.function(jit_compile=jit_compile)(self.compute_atom_cost_params)
        self.apply_alignment_and_shifts = tf.function(jit_compile=jit_compile)(self.apply_alignment_and_shifts)
        self.compute_theo_proj = tf.function(jit_compile=jit_compile)(self.compute_theo_proj)
        self.__call__ = tf.function(jit_compile=jit_compile)(self.__call__)

    # @tf.function(jit_compile=True)
    def prepare_batch(self, indexes):
        # images, indexes = x

        # Update batch_size (in case it is incomplete)
        batch_size_scope = tf.shape(indexes)[0]

        # Precompute batch alignments
        if self.generator.refinePose:
            rot_batch = tf.gather(self.generator.angle_rot, indexes, axis=0)
            tilt_batch = tf.gather(self.generator.angle_tilt, indexes, axis=0)
            psi_batch = tf.gather(self.generator.angle_psi, indexes, axis=0)
        else:
            rot_batch = tf.gather(self.generator.angle_rot, indexes, axis=0)
            tilt_batch = tf.gather(self.generator.angle_tilt, indexes, axis=0)
            psi_batch = tf.gather(self.generator.angle_psi, indexes, axis=0)

        shifts_x = tf.gather(self.generator.shifts[0], indexes, axis=0)
        shifts_y = tf.gather(self.generator.shifts[1], indexes, axis=0)

        # Precompute batch CTFs
        defocusU_batch = tf.gather(self.generator.defocusU, indexes, axis=0)
        defocusV_batch = tf.gather(self.generator.defocusV, indexes, axis=0)
        defocusAngle_batch = tf.gather(self.generator.defocusAngle, indexes, axis=0)
        cs_batch = tf.gather(self.generator.cs, indexes, axis=0)
        kv_batch = self.generator.kv
        ctf = computeCTF(defocusU_batch, defocusV_batch, defocusAngle_batch, cs_batch, kv_batch,
                         self.generator.sr, self.generator.pad_factor,
                         [self.generator.xsize, int(0.5 * self.generator.xsize + 1)],
                         batch_size_scope, self.generator.applyCTF)

        return [rot_batch, tilt_batch, psi_batch], [shifts_x, shifts_y], ctf

    # @tf.function(jit_compile=True)
    def compute_field_volume(self, decoder_inputs_x, decoder_inputs_y, decoder_inputs_z):
        # Compute deformation field
        d_x = self.generator.computeDeformationFieldVol(decoder_inputs_x)
        d_y = self.generator.computeDeformationFieldVol(decoder_inputs_y)
        d_z = self.generator.computeDeformationFieldVol(decoder_inputs_z)

        # Apply deformation field
        c_x = self.generator.applyDeformationFieldVol(d_x, 0)
        c_y = self.generator.applyDeformationFieldVol(d_y, 1)
        c_z = self.generator.applyDeformationFieldVol(d_z, 2)

        return c_x, c_y, c_z

    # @tf.function(jit_compile=True)
    def compute_field_atoms(self, decoder_inputs_x, decoder_inputs_y, decoder_inputs_z):
        # Compute atoms deformation field
        da_x = self.generator.computeDeformationFieldAtoms(decoder_inputs_x)
        da_y = self.generator.computeDeformationFieldAtoms(decoder_inputs_y)
        da_z = self.generator.computeDeformationFieldAtoms(decoder_inputs_z)

        # Apply deformation field
        a_x = self.generator.applyDeformationFieldAtoms(da_x, 0)
        a_y = self.generator.applyDeformationFieldAtoms(da_y, 1)
        a_z = self.generator.applyDeformationFieldAtoms(da_z, 2)

        return a_x, a_y, a_z

    # @tf.function(jit_compile=True)
    def compute_atom_cost_params(self, a_x, a_y, a_z):
        bondk = self.generator.calcBond([a_x, a_y, a_z])
        anglek = self.generator.calcAngle([a_x, a_y, a_z])
        coords = self.generator.calcCoords([a_x, a_y, a_z])

        return bondk, anglek, coords

    # @tf.function(jit_compile=True)
    def apply_alignment_and_shifts(self, c_x, c_y, c_z, alignments, shifts, delta_euler, delta_shifts):
        # Apply alignment
        c_r_x = self.generator.applyAlignmentDeltaEuler([c_x, c_y, c_z, delta_euler], alignments, 0)
        c_r_y = self.generator.applyAlignmentDeltaEuler([c_x, c_y, c_z, delta_euler], alignments, 1)

        # Apply shifts
        c_r_s_x = self.generator.applyDeltaShifts([c_r_x, delta_shifts], shifts, 0)
        c_r_s_y = self.generator.applyDeltaShifts([c_r_y, delta_shifts], shifts, 1)

        return c_r_s_x, c_r_s_y

    # @tf.function(jit_compile=True)
    def compute_theo_proj(self, c_x, c_y, ctf):
        # Scatter image and bypass gradient
        decoded = self.generator.scatterImgByPass([c_x, c_y])

        if self.generator.step > 1 or self.generator.ref_is_struct:
            # Gaussian filter image
            decoded = self.generator.gaussianFilterImage(decoded)

            if self.CTF == "apply":
                # CTF filter image
                decoded = self.generator.ctfFilterImage(decoded, ctf)
        else:
            if self.CTF == "apply":
                # CTF filter image
                decoded = self.generator.ctfFilterImage(decoded, ctf)

        return decoded

    # @tf.function(jit_compile=False)
    def __call__(self, x):
        # encoded, images, indexes = x
        encoded, indexes = x
        alignments, shifts, ctf = self.prepare_batch(indexes)

        decoder_inputs_x, decoder_inputs_y, decoder_inputs_z, delta_euler, delta_shifts = encoded

        # Compute deformation field
        c_x, c_y, c_z = self.compute_field_volume(decoder_inputs_x, decoder_inputs_y, decoder_inputs_z)

        # Bond and angle
        if self.generator.ref_is_struct:
            # Compute atoms deformation field
            a_x, a_y, a_z = self.compute_field_atoms(decoder_inputs_x, decoder_inputs_y, decoder_inputs_z)

            bondk, anglek, coords = self.compute_atom_cost_params(a_x, a_y, a_z)

        else:
            bondk = 0.0
            anglek = 0.0
            coords = 0.0

        # Apply alignment and shifts
        c_r_s_x, c_r_s_y = self.apply_alignment_and_shifts(c_x, c_y, c_z, alignments, shifts, delta_euler, delta_shifts)

        # Theoretical projections
        decoded = self.compute_theo_proj(c_r_s_x, c_r_s_y, ctf)

        return decoded, bondk, anglek, coords, ctf


class AutoEncoder(tf.keras.Model):
    def __init__(self, generator, architecture="convnn", CTF="apply", mode=None, l_bond=0.01, l_angle=0.01,
                 l_clashes=None, jit_compile=True, **kwargs):
        super(AutoEncoder, self).__init__(**kwargs)
        self.generator = generator
        self.CTF = CTF
        self.refPose = 1.0 if generator.refinePose else 0.0
        self.mode = generator.mode if mode is None else mode
        self.l_bond = l_bond
        self.l_angle = l_angle
        self.l_clashes = l_clashes
        self.encoder = Encoder(generator.zernike_size.shape[0], generator.xsize,
                               generator.refinePose, architecture=architecture,
                               mode=self.mode, jit_compile=jit_compile)
        self.decoder = Decoder(generator, CTF=CTF, jit_compile=jit_compile)
        self.total_loss_tracker = tf.keras.metrics.Mean(name="total_loss")
        self.img_loss_tracker = tf.keras.metrics.Mean(name="img_loss")
        self.bond_loss_tracker = tf.keras.metrics.Mean(name="bond_loss")
        self.angle_loss_tracker = tf.keras.metrics.Mean(name="angle_loss")
        self.clash_loss_tracker = tf.keras.metrics.Mean(name="clash_loss")

        if allow_open3d:
            # Continuous convolution
            # k_clash = 0.6  # Repulsion value (for bb)
            # extent = 1.2  # 2 * radius, typical class distance between 0.4A-0.6A (for bb)
            self.k_clash = 4.  # Repulsion value
            self.extent = 8.  # 2 * radius, typical class distance between 0.4A-0.6A
            self.fn = lambda x, y: simple_clash(x, y, self.k_clash)
            self.conv = ml3d.layers.ContinuousConv(1, kernel_size=[3, 3, 3],
                                                   activation=None, use_bias=False,
                                                   trainable=False, kernel_initializer=tf.keras.initializers.Ones(),
                                                   kernel_regularizer=None,
                                                   normalize=False,
                                                   radius_search_metric="L2",
                                                   coordinate_mapping="identity",
                                                   interpolation="nearest_neighbor",
                                                   window_function=None, radius_search_ignore_query_points=True)
            self.nsearch = ml3d.layers.FixedRadiusSearch(return_distances=True, ignore_query_point=True)

    @property
    def metrics(self):
        return [
            self.total_loss_tracker,
            self.img_loss_tracker,
            self.bond_loss_tracker,
            self.angle_loss_tracker,
            self.clash_loss_tracker
        ]

    def train_step(self, data):
        inputs = data[0]

        if self.mode == "spa":
            indexes = data[1]
            images = inputs
        elif self.mode == "tomo":
            indexes = data[1][0]
            images = inputs[0]

        # self.decoder.generator.indexes = indexes
        # self.decoder.generator.current_images = images

        # Precompute batch zernike coefficients
        z_x_batch = tf.gather(self.generator.z_x_space, indexes, axis=0)
        z_y_batch = tf.gather(self.generator.z_y_space, indexes, axis=0)
        z_z_batch = tf.gather(self.generator.z_z_space, indexes, axis=0)

        # Row splits
        B = tf.shape(images)[0]
        # num_points = self.generator.atom_coords.shape[0]
        num_points = tf.cast(tf.shape(self.generator.ca_indices)[0], tf.int64)
        points_row_splits = tf.range(B + 1, dtype=tf.int64) * num_points
        queries_row_splits = tf.range(B + 1, dtype=tf.int64) * num_points

        # Prepare batch
        # images = self.decoder.prepare_batch([images, indexes])

        if self.mode == "spa":
            inputs = images
        elif self.mode == "tomo":
            inputs[0] = images

        with tf.GradientTape() as tape:
            encoded = self.encoder(inputs)
            encoded[0] = encoded[0] + z_x_batch
            encoded[1] = encoded[1] + z_y_batch
            encoded[2] = encoded[2] + z_z_batch
            encoded[3] *= self.refPose
            encoded[4] *= self.refPose
            decoded, bondk, anglek, coords, ctf = self.decoder([encoded, indexes])

            if self.CTF == "wiener":
                images = self.generator.wiener2DFilter(images, ctf)

            if allow_open3d:
                # Fixed radius search
                result = self.nsearch(coords, coords, 0.5 * self.extent, points_row_splits, queries_row_splits)


                # Compute neighbour distances
                clashes = self.conv(tf.ones((tf.shape(coords)[0], 1), tf.float32), coords, coords, self.extent,
                                    user_neighbors_row_splits=result.neighbors_row_splits,
                                    user_neighbors_index=result.neighbors_index,
                                    user_neighbors_importance=self.fn(result.neighbors_distance, result.neighbors_row_splits))
                clashes = tf.reduce_mean(tf.reshape(clashes, (B, -1)), axis=-1)
            else:
                clashes = 0.0

            img_loss = self.generator.cost_function(images, decoded)

            # Bond and angle losses
            if self.decoder.generator.ref_is_struct:
                bond_loss = tf.sqrt(tf.reduce_mean(tf.keras.losses.MSE(self.generator.bond0, bondk)))
                angle_loss = tf.sqrt(tf.reduce_mean(tf.keras.losses.MSE(self.generator.angle0, anglek)))
            else:
                bond_loss, angle_loss = 0.0, 0.0

            total_loss = img_loss + self.l_bond * bond_loss + self.l_angle * angle_loss + self.l_clashes * clashes

        grads = tape.gradient(total_loss, self.trainable_weights)
        self.optimizer.apply_gradients(zip(grads, self.trainable_weights))
        self.total_loss_tracker.update_state(total_loss)
        self.img_loss_tracker.update_state(img_loss)
        self.angle_loss_tracker.update_state(angle_loss)
        self.bond_loss_tracker.update_state(bond_loss)
        self.clash_loss_tracker.update_state(clashes)
        return {
            "loss": self.total_loss_tracker.result(),
            "img_loss": self.img_loss_tracker.result(),
            "bond": self.bond_loss_tracker.result(),
            "angle": self.angle_loss_tracker.result(),
            "clashes": self.clash_loss_tracker.result(),
        }

    def test_step(self, data):
        inputs = data[0]

        if self.mode == "spa":
            indexes = data[1]
            images = inputs
        elif self.mode == "tomo":
            indexes = data[1][0]
            images = inputs[0]

        # self.decoder.generator.indexes = indexes
        # self.decoder.generator.current_images = images

        # Precompute batch zernike coefficients
        z_x_batch = tf.gather(self.generator.z_x_space, indexes, axis=0)
        z_y_batch = tf.gather(self.generator.z_y_space, indexes, axis=0)
        z_z_batch = tf.gather(self.generator.z_z_space, indexes, axis=0)

        # Row splits
        B = tf.shape(images)[0]
        # num_points = self.generator.atom_coords.shape[0]
        num_points = tf.cast(tf.shape(self.generator.ca_indices)[0], tf.int64)
        points_row_splits = tf.range(B + 1, dtype=tf.int64) * num_points
        queries_row_splits = tf.range(B + 1, dtype=tf.int64) * num_points

        # Prepare batch
        # images = self.decoder.prepare_batch([images, indexes])

        if self.mode == "spa":
            inputs = images
        elif self.mode == "tomo":
            inputs[0] = images

        encoded = self.encoder(inputs)
        encoded[0] = encoded[0] + z_x_batch
        encoded[1] = encoded[1] + z_y_batch
        encoded[2] = encoded[2] + z_z_batch
        encoded[3] *= self.refPose
        encoded[4] *= self.refPose
        decoded, bondk, anglek, coords, ctf = self.decoder([encoded, indexes])

        if self.CTF == "wiener":
            images = self.generator.wiener2DFilter(images, ctf)

        if allow_open3d:
            # Fixed radius search
            result = self.nsearch(coords, coords, 0.5 * self.extent, points_row_splits, queries_row_splits)

            # Compute neighbour distances
            clashes = self.conv(tf.ones((tf.shape(coords)[0], 1), tf.float32), coords, coords, self.extent,
                                user_neighbors_row_splits=result.neighbors_row_splits,
                                user_neighbors_index=result.neighbors_index,
                                user_neighbors_importance=self.fn(result.neighbors_distance,
                                                                  result.neighbors_row_splits))
            clashes = tf.reduce_mean(tf.reshape(clashes, (B, -1)), axis=-1)
        else:
            clashes = 0.0

        img_loss = self.generator.cost_function(images, decoded)

        # Bond and angle losses
        if self.decoder.generator.ref_is_struct:
            bond_loss = tf.sqrt(tf.reduce_mean(tf.keras.losses.MSE(self.generator.bond0, bondk)))
            angle_loss = tf.sqrt(tf.reduce_mean(tf.keras.losses.MSE(self.generator.angle0, anglek)))
        else:
            bond_loss, angle_loss = 0.0, 0.0

        total_loss = img_loss + self.l_bond * bond_loss + self.l_angle * angle_loss + self.l_clashes * clashes

        self.total_loss_tracker.update_state(total_loss)
        self.img_loss_tracker.update_state(img_loss)
        self.angle_loss_tracker.update_state(angle_loss)
        self.bond_loss_tracker.update_state(bond_loss)
        self.clash_loss_tracker.update_state(clashes)
        return {
            "loss": self.total_loss_tracker.result(),
            "img_loss": self.img_loss_tracker.result(),
            "bond": self.bond_loss_tracker.result(),
            "angle": self.angle_loss_tracker.result(),
            "clashes": self.clash_loss_tracker.result(),
        }

    def predict_step(self, data):
        inputs = data[0]

        if self.mode == "spa":
            indexes = data[1]
            # images = inputs
        elif self.mode == "tomo":
            indexes = data[1][0]
            # images = inputs[0]

        # Precompute batch zernike coefficients
        self.decoder.generator.z_x_batch = tf.gather(self.generator.z_x_space, indexes, axis=0)
        self.decoder.generator.z_y_batch = tf.gather(self.generator.z_y_space, indexes, axis=0)
        self.decoder.generator.z_z_batch = tf.gather(self.generator.z_z_space, indexes, axis=0)

        # if self.CTF == "wiener":
        #     images = self.decoder.generator.wiener2DFilter(images)
        #     if self.mode == "spa":
        #         inputs = images
        #     elif self.mode == "tomo":
        #         inputs[0] = images

        encoded = self.encoder(inputs)
        encoded[0] = encoded[0] + self.decoder.generator.z_x_batch
        encoded[1] = encoded[1] + self.decoder.generator.z_y_batch
        encoded[2] = encoded[2] + self.decoder.generator.z_z_batch
        encoded[3] *= self.refPose
        encoded[4] *= self.refPose

        return encoded

    def call(self, input_features):
        if allow_open3d:
            # To know this weights exist
            coords = tf.zeros((1, 3), tf.float32)
            _ = self.nsearch(coords, coords, 1.0)
            _ = self.conv(tf.ones((tf.shape(coords)[0], 1), tf.float32), coords, coords, self.extent)

        indexes = tf.zeros(tf.shape(input_features)[0], dtype=tf.int32)
        return self.decoder([self.encoder(input_features), indexes])
