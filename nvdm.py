"""NVDM Tensorflow implementation by Yishu Miao"""
from __future__ import print_function

import numpy as np
import tensorflow as tf
import math
import os
import utils as utils
import csv
import itertools
import datetime
import pdb
import time
import sys

train_csv_filename = ''
dev_csv_filename = ''
test_csv_filename = ''

np.random.seed(0)
tf.set_random_seed(0)




class NVDM(object):
    """ Neural Variational Document Model -- BOW VAE.
    """
    def __init__(self, 
                 vocab_size,
                 n_hidden,
                 n_topic, 
                 n_sample,
                 learning_rate, 
                 batch_size,
                 non_linearity):
        self.vocab_size = vocab_size
        self.n_hidden = n_hidden
        self.n_topic = n_topic
        self.n_sample = n_sample
        self.non_linearity = non_linearity
        self.learning_rate = learning_rate
        self.batch_size = batch_size

        self.x = tf.placeholder(tf.float32, [None, vocab_size], name='input')
        self.mask = tf.placeholder(tf.float32, [None], name='mask')  # mask paddings

        # encoder
        with tf.variable_scope('encoder'): 
          self.enc_vec = utils.mlp(self.x, [self.n_hidden], self.non_linearity)
          self.mean = utils.linear(self.enc_vec, self.n_topic, scope='mean')
          self.logsigm = utils.linear(self.enc_vec, 
                                     self.n_topic, 
                                     bias_start_zero=True,
                                     matrix_start_zero=True,
                                     scope='logsigm')
          self.kld = -0.5 * tf.reduce_sum(1 - tf.square(self.mean) + 2 * self.logsigm - tf.exp(2 * self.logsigm), 1)
          self.kld = self.mask*self.kld  # mask paddings
        
        with tf.variable_scope('decoder'):
          if self.n_sample ==1:  # single sample
            eps = tf.random_normal((batch_size, self.n_topic), 0, 1)
            doc_vec = tf.multiply(tf.exp(self.logsigm), eps) + self.mean
            logits = tf.nn.log_softmax(utils.linear(doc_vec, self.vocab_size, scope='projection'))
            self.recons_loss = -tf.reduce_sum(tf.multiply(logits, self.x), 1)
          # multiple samples
          else:
            eps = tf.random_normal((self.n_sample*batch_size, self.n_topic), 0, 1)
            eps_list = tf.split(eps, self.n_sample, 0)
            recons_loss_list = []
            for i in xrange(self.n_sample):
              if i > 0: tf.get_variable_scope().reuse_variables()
              curr_eps = eps_list[i]
              doc_vec = tf.multiply(tf.exp(self.logsigm), curr_eps) + self.mean
              logits = tf.nn.log_softmax(utils.linear(doc_vec, self.vocab_size, scope='projection'))
              recons_loss_list.append(-tf.reduce_sum(tf.multiply(logits, self.x), 1))
            self.recons_loss = tf.add_n(recons_loss_list) / self.n_sample

        self.objective = self.recons_loss + self.kld

        optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate)
        fullvars = tf.trainable_variables()

        enc_vars = utils.variable_parser(fullvars, 'encoder')
        dec_vars = utils.variable_parser(fullvars, 'decoder')

        enc_grads = tf.gradients(self.objective, enc_vars)
        dec_grads = tf.gradients(self.objective, dec_vars)

        self.optim_enc = optimizer.apply_gradients(zip(enc_grads, enc_vars))
        self.optim_dec = optimizer.apply_gradients(zip(dec_grads, dec_vars))

def train(sess, model, 
          train_url, 
          test_url, 
          batch_size,
          FLAGS,
          train_csv_filename,
          dev_csv_filename,
          test_csv_filename, 
          training_epochs=1000, 
          alternate_epochs=10,is_restore=False,current_setting='N/A'):
  """train nvdm model."""
  train_set, train_count = utils.data_set(train_url)
  test_set, test_count = utils.data_set(test_url)
  # hold-out development dataset
  dev_set = test_set[:50]
  dev_count = test_count[:50]

  dev_batches = utils.create_batches(len(dev_set), batch_size, shuffle=False)
  test_batches = utils.create_batches(len(test_set), batch_size, shuffle=False)
  #save model
  saver = tf.train.Saver()
  
  if is_restore:
      saver.restore(sess, "./checkpoints/model.ckpt")
  
  for epoch in range(training_epochs):
    train_batches = utils.create_batches(len(train_set), batch_size, shuffle=True)
    #-------------------------------
    # train
    for switch in xrange(0, 2):
      if switch == 0:
        optim = model.optim_dec
        print_mode = 'updating decoder'
      else:
        optim = model.optim_enc
        print_mode = 'updating encoder'
      for i in xrange(alternate_epochs):
        loss_sum = 0.0
        ppx_sum = 0.0
        kld_sum = 0.0
        word_count = 0
        doc_count = 0
        for idx_batch in train_batches:
          data_batch, count_batch, mask = utils.fetch_data(
          train_set, train_count, idx_batch, FLAGS.vocab_size)
          input_feed = {model.x.name: data_batch, model.mask.name: mask}
          _, (loss, kld) = sess.run((optim, 
                                    [model.objective, model.kld]),
                                    input_feed)
          loss_sum += np.sum(loss)
          kld_sum += np.sum(kld) / np.sum(mask) 
          word_count += np.sum(count_batch)
          # to avoid nan error
          count_batch = np.add(count_batch, 1e-12)
          # per document loss
          ppx_sum += np.sum(np.divide(loss, count_batch)) 
          doc_count += np.sum(mask)
        print_ppx = np.exp(loss_sum / word_count)
        print_ppx_perdoc = np.exp(ppx_sum / doc_count)
        print_kld = kld_sum/len(train_batches)
        
        with open(train_csv_filename, 'a') as train_csv:
          train_writer = csv.writer(train_csv, delimiter= ',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
          train_writer.writerow([epoch+1, print_mode, i,  print_ppx, print_ppx_perdoc, print_kld])
        print(current_setting)
        print('| Epoch train: {:d} |'.format(epoch+1), 
               print_mode, '{:d}'.format(i),
               '| Corpus ppx: {:.5f}'.format(print_ppx),  # perplexity for all docs
               '| Per doc ppx: {:.5f}'.format(print_ppx_perdoc),  # perplexity for per doc
               '| KLD: {:.5}'.format(print_kld))
    #-------------------------------
    # dev
    loss_sum = 0.0
    kld_sum = 0.0
    ppx_sum = 0.0
    word_count = 0
    doc_count = 0
    for idx_batch in dev_batches:
      data_batch, count_batch, mask = utils.fetch_data(
          dev_set, dev_count, idx_batch, FLAGS.vocab_size)
      input_feed = {model.x.name: data_batch, model.mask.name: mask}
      loss, kld = sess.run([model.objective, model.kld],
                           input_feed)
      loss_sum += np.sum(loss)
      kld_sum += np.sum(kld) / np.sum(mask)  
      word_count += np.sum(count_batch)
      count_batch = np.add(count_batch, 1e-12)
      ppx_sum += np.sum(np.divide(loss, count_batch))
      doc_count += np.sum(mask) 
    print_ppx = np.exp(loss_sum / word_count)
    print_ppx_perdoc = np.exp(ppx_sum / doc_count)
    print_kld = kld_sum/len(dev_batches)
    print(current_setting)
    with open(dev_csv_filename, 'a') as dev_csv:
      dev_writer = csv.writer(dev_csv, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
      dev_writer.writerow([epoch+1, print_ppx, print_ppx_perdoc, print_kld])
        
    print('| Epoch dev: {:d} |'.format(epoch+1), 
           '| Perplexity: {:.9f}'.format(print_ppx),
           '| Per doc ppx: {:.5f}'.format(print_ppx_perdoc),
           '| KLD: {:.5}'.format(print_kld))        
    #-------------------------------
    # test
    if FLAGS.test:
      loss_sum = 0.0
      kld_sum = 0.0
      ppx_sum = 0.0
      word_count = 0
      doc_count = 0
      for idx_batch in test_batches:
        data_batch, count_batch, mask = utils.fetch_data(
          test_set, test_count, idx_batch, FLAGS.vocab_size)
        input_feed = {model.x.name: data_batch, model.mask.name: mask}
        loss, kld = sess.run([model.objective, model.kld],
                             input_feed)
        loss_sum += np.sum(loss)
        kld_sum += np.sum(kld)/np.sum(mask) 
        word_count += np.sum(count_batch)
        count_batch = np.add(count_batch, 1e-12)
        ppx_sum += np.sum(np.divide(loss, count_batch))
        doc_count += np.sum(mask) 
      print_ppx = np.exp(loss_sum / word_count)
      print_ppx_perdoc = np.exp(ppx_sum / doc_count)
      print_kld = kld_sum/len(test_batches)
      print(current_setting)
      with open(test_csv_filename, 'a') as test_csv:
        test_writer = csv.writer(test_csv, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
        test_writer.writerow([epoch+1, print_ppx, print_ppx_perdoc, print_kld])
        
      print('| Epoch test: {:d} |'.format(epoch+1), 
             '| Perplexity: {:.9f}'.format(print_ppx),
             '| Per doc ppx: {:.5f}'.format(print_ppx_perdoc),
             '| KLD: {:.5}'.format(print_kld))
      
    #create a check point after 50 epochs
    #if epoch % 50 == 0:
  save_path = saver.save(sess,'./checkpoints/model_{}_{}_{}.ckpt'.format(FLAGS.n_sample,FLAGS.n_hidden,FLAGS.n_topics))
  print("Model saved in path: %s" % save_path)
      
class flag:
    def __init__(self,n_sample,n_hidden,n_topics):
        self.learning_rate=5e-5
        self.batch_size=64
        self.n_hidden=n_hidden
        self.n_topic= n_topics
        self.n_sample=n_sample
        self.vocab_size= 2000
        self.test=True
        self.non_linearity='tanh'
	  
def main(argv=None):
    print('Version 5')
    
    train_url = os.path.join('data/20news', 'train.feat')
    test_url = os.path.join('data/20news', 'test.feat')
	
    settings_n_topics = [50,100,200]
    settings_n_hidden = [300,500]
    settings_n_sample = [1,5]
    settings = list(itertools.product(settings_n_hidden,settings_n_topics,settings_n_sample))
    configure_setting = int(sys.argv[1])
    print('configure setting: {}'.format(configure_setting))
    for setting in settings[configure_setting*2:(configure_setting+1)*2]:
        # start timer
        print('-'*30)
        current_setting = str(setting)
        print(setting)
        start_time = time.time()
	
        (n_sample,n_hidden,n_topics) = setting
        print('model params n_sample: {} n_hidden: {} n_topics: {}'.format(n_sample,n_hidden,n_topics))
        time_stamp = '{:%Y-%m-%d-%H-%M-%S}'.format(datetime.datetime.now())
        train_csv_filename = './log/train_output_{}_{}_{}_{}.csv'.format(n_sample,n_hidden,n_topics,time_stamp)
        dev_csv_filename = './log/dev_output_{}_{}_{}_{}.csv'.format(n_sample,n_hidden,n_topics,time_stamp)
        test_csv_filename = './log/test_output_{}_{}_{}_{}.csv'.format(n_sample,n_hidden,n_topics,time_stamp)
        time_log_filename = './log/time_elapsed_{}_{}_{}.txt'.format(n_sample,n_hidden,n_topics)

        with open(train_csv_filename, 'w') as train_csv:
            train_writer = csv.writer(train_csv, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            train_writer.writerow(['Train Epoch', 'Encoder/Decoder', 'Num', 'Corpus ppx', 'Per doc ppx', 'KLD'])
        with open(dev_csv_filename, 'w') as dev_csv:
            dev_writer = csv.writer(dev_csv, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            dev_writer.writerow(['Dev Epoch', 'Perplexity', 'Per doc ppx', 'KLD'])
        with open(test_csv_filename, 'w') as test_csv:
            test_writer = csv.writer(test_csv, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            test_writer.writerow(['Test Epoch', 'Perplexity', 'Per doc ppx', 'KLD'])

        FLAGS = flag(n_sample,n_hidden,n_topics)
        #pdb.set_trace()
        if FLAGS.non_linearity == 'tanh':
          non_linearity = tf.nn.tanh
        elif FLAGS.non_linearity == 'sigmoid':
          non_linearity = tf.nn.sigmoid
        else:
          non_linearity = tf.nn.relu
        sess = tf.Session()
        #with tf.device('/device:GPU:0'):
        nvdm = NVDM(vocab_size=FLAGS.vocab_size,
                    n_hidden=FLAGS.n_hidden,
                    n_topic=FLAGS.n_topic, 
                    n_sample=FLAGS.n_sample,
                    learning_rate=FLAGS.learning_rate, 
                    batch_size=FLAGS.batch_size,
                    non_linearity=non_linearity)
           
        init = tf.global_variables_initializer()
        sess.run(init)
        train(sess, nvdm, train_url, test_url, FLAGS.batch_size,FLAGS,train_csv_filename,dev_csv_filename,test_csv_filename,current_setting=current_setting,training_epochs=1)
        sess.close()
        tf.reset_default_graph()
	
	# stop timer and write to file
        elapsed_time = time.time() - start_time

        with open(time_log_filename, 'w') as time_log:
          time_log.write("Time Stamp: " + str(time_stamp) + "\n" + "Time Elapsed: " + str(elapsed_time))
	
        time_log.close()
		
if __name__ == '__main__':
    tf.app.run()
