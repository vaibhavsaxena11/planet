# Copyright 2019 The PlaNet Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
from tensorflow_probability import distributions as tfd

from planet import tools
from planet.models import base


class RSSM_prod(base.Base):
  """Deterministic and stochastic state model.

  The stochastic latent is computed from the hidden state at the same time
  step. If an observation is present, the posterior latent is compute from both
  the hidden state and the observation.

  Prior:    Posterior:

  (a)       (a)
     \         \
      v         v
  [h]->[h]  [h]->[h]
      ^ |       ^ :
     /  v      /  v
  (s)  (s)  (s)  (s)
                  ^
                  :
                 (o)
  """

  def __init__(
      self, state_size, belief_size, embed_size,
      future_mix=False, mean_only=False, min_stddev=0.1):
    self._state_size = state_size
    self._belief_size = belief_size
    self._embed_size = embed_size
    self._future_mix = future_mix
    self._cell = tf.contrib.rnn.GRUBlockCell(self._belief_size)
    self._kwargs = dict(units=self._embed_size, activation=tf.nn.relu)
    self._mean_only = mean_only
    self._min_stddev = min_stddev
    super(RSSM_prod, self).__init__(
        tf.make_template('transition', self._transition),
        tf.make_template('posterior', self._posterior))

  @property
  def state_size(self):
    return {
        'mean': self._state_size,
        'stddev': self._state_size,
        'sample': self._state_size,
        'belief': self._belief_size,
        'rnn_state': self._belief_size,
    }

  def dist_from_state(self, state, mask=None):
    """Extract the latent distribution from a prior or posterior state."""
    if mask is not None:
      stddev = tools.mask(state['stddev'], mask, value=1)
    else:
      stddev = state['stddev']
    dist = tfd.MultivariateNormalDiag(state['mean'], stddev)
    return dist

  def features_from_state(self, state):
    """Extract features for the decoder network from a prior or posterior."""
    return tf.concat([state['sample'], state['belief']], -1)

  def divergence_from_states(self, lhs, rhs, mask):
    """Compute the divergence measure between two states."""
    lhs = self.dist_from_state(lhs, mask)
    rhs = self.dist_from_state(rhs, mask)
    return tools.mask(tfd.kl_divergence(lhs, rhs), mask)

  def _transition(self, prev_state, prev_action, zero_obs):
    """Compute prior next state by applying the transition dynamics."""
    inputs = tf.concat([prev_state['sample'], prev_action], -1)
    hidden = tf.layers.dense(inputs, **self._kwargs)
    belief, rnn_state = self._cell(hidden, prev_state['rnn_state'])
    if self._future_mix:
      hidden = belief
    hidden = tf.layers.dense(hidden, **self._kwargs)
    mean = tf.layers.dense(hidden, self._state_size, None)
    stddev = tf.layers.dense(hidden, self._state_size, tf.nn.softplus)
    stddev += self._min_stddev
    if self._mean_only:
      sample = mean
    else:
      sample = tfd.MultivariateNormalDiag(mean, stddev).sample()
    return {
        'mean': mean,
        'stddev': stddev,
        'sample': sample,
        'belief': belief,
        'rnn_state': rnn_state,
    }

#   def _posterior(self, prev_state, prev_action, obs):
#     """Compute posterior state from previous state and current observation."""
#     prior = self._transition_tpl(prev_state, prev_action, tf.zeros_like(obs))
#     inputs = tf.concat([prior['belief'], obs], -1)
#     hidden = tf.layers.dense(inputs, **self._kwargs)
#     mean = tf.layers.dense(hidden, self._state_size, None)
#     stddev = tf.layers.dense(hidden, self._state_size, tf.nn.softplus)
#     stddev += self._min_stddev
#     if self._mean_only:
#       sample = mean
#     else:
#       sample = tfd.MultivariateNormalDiag(mean, stddev).sample()
#     return {
#         'mean': mean,
#         'stddev': stddev,
#         'sample': sample,
#         'belief': prior['belief'],
#         'rnn_state': prior['rnn_state'],
#     }

  def _posterior(self, prev_state, prev_action, obs):
    """Compute posterior state from previous state and current observation."""
    prior = self._transition_tpl(prev_state, prev_action, tf.zeros_like(obs))
    mean_1 = prior['mean']
    stddev_1 = prior['stddev']
    # inputs = tf.concat([prior['belief'], obs], -1)
    inputs = obs ## (50, 1024)
    hidden = tf.layers.dense(inputs, **self._kwargs)
    mean_2 = tf.layers.dense(hidden, self._state_size, None)
    stddev_2 = tf.layers.dense(hidden, self._state_size, tf.nn.softplus)
    stddev_2 += self._min_stddev
    
    cov_mat_1 = tf.matrix_diag(tf.math.square(stddev_1))
    cov_mat_2 = tf.matrix_diag(tf.math.square(stddev_2))
    cov_mat_12_inv = tf.matrix_diag(tf.reciprocal(tf.math.square(stddev_1) + tf.math.square(stddev_2)))
    mean = tf.matmul(tf.matmul(cov_mat_2, cov_mat_12_inv), tf.expand_dims(mean_1, axis=-1)) + tf.matmul(tf.matmul(cov_mat_1, cov_mat_12_inv), tf.expand_dims(mean_2, axis=-1)) # (50, 30, 1)
    mean = tf.squeeze(mean, axis=[-1]) # (50, 30)
    # import pdb; pdb.set_trace()
    stddev = tf.math.sqrt(tf.matrix_diag_part( tf.matmul(cov_mat_1, tf.matmul(cov_mat_12_inv, cov_mat_2)) ))
    if self._mean_only:
      sample = mean
    else:
      sample = tfd.MultivariateNormalDiag(mean, stddev).sample()
    return {
        'mean': mean,
        'stddev': stddev,
        'sample': sample,
        'belief': prior['belief'],
        'rnn_state': prior['rnn_state'],
    }
