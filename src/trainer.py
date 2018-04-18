"""Trainer module for training seq2seq model
"""

import json
import os
import pickle
from datetime import datetime
from tqdm import tqdm
import itertools

import numpy as np
import torch
import torch.nn as nn
from torch import optim
from torch.autograd import Variable

import constants as C
from models.model import LM
from loss import Loss

USE_CUDA = torch.cuda.is_available()

class TrainerMetrics:

    def __init__(self, logger):
        self.train_loss = []
        self.dev_loss = []

        self.train_perplexity = []
        self.dev_perplexity = []

        self.logger = logger

    def add_loss(self, loss, mode):
        epoch_loss = loss.epoch_loss()
        epoch_perplexity = loss.epoch_perplexity()
        
        if mode == C.TRAIN_TYPE:
            self.train_loss.append(epoch_loss)
            self.train_perplexity.append(epoch_perplexity)
            min_loss, min_perplexity = np.nanmin(self.train_loss), np.nanmin(self.train_perplexity)
        elif mode == C.DEV_TYPE:
            self.dev_loss.append(epoch_loss)
            self.dev_perplexity.append(epoch_perplexity)
            min_loss, min_perplexity = np.nanmin(self.dev_loss), np.nanmin(self.dev_perplexity)
        else:
            raise 'Unimplemented mode: %s' % mode

        mode = mode.upper()
        self.logger.log('\n\t[%s] Loss = %.4f, Min [%s] Loss = %.4f' % (mode, epoch_loss, mode, min_loss))
        self.logger.log('\n\[%s] Perplexity = %.2f, Min [%s] Perplexity = %.2f' % (mode, epoch_perplexity, mode, min_perplexity))

    def is_best_dev_loss(self):
        return len(self.dev_loss) > 0 and self.dev_loss[-1] == np.nanmin(self.dev_loss)

class Trainer:

    def __init__(self, 
        dataloader, params,
        random_seed=1,
        save_model_every=1,     # Every Number of epochs to save after
        print_every=1000,       # Every Number of batches to print after
        dev_loader=None,
        #test_loader=None,
        vocab=None,
        logger=None,
        resume_training=False,
        resume_epoch=None,
        save_dir=None
    ):
        _set_random_seeds(random_seed)

        self.save_model_every = save_model_every
        self.print_every = print_every
        self.params = params
        self.vocab = vocab
        self.model_name = params[C.MODEL_NAME]
        self.start_epoch = 0

        # Data Loaders
        self.dataloader = dataloader
        self.dev_loader = dev_loader
        #self.test_loader = test_loader

        # Logger
        self.logger = logger

        # Model
        self.model = LM(
            self.vocab.get_vocab_size(),
            hsizes(params, self.model_name),
            params[C.EMBEDDING_DIM],
            params[C.OUTPUT_MAX_LEN],
            params[C.H_LAYERS],
            params[C.DROPOUT],
            params[C.MODEL_NAME]
        ) if self.dataloader else None
        self.logger.log('MODEL : %s' % self.model)
        self.logger.log('PARAMS: %s' % self.params)

        if resume_training:
            self.save_dir = save_dir
            self.load_model_optimizer(resume_epoch)
            self.start_epoch = resume_epoch + 1
        else:
            self.save_dir = self._save_dir(datetime.now())

        # Optimizer and loss metrics
        self.optimizer = None
        self.loss = Loss()
        self.metrics = TrainerMetrics(logger)

        if USE_CUDA:
            if self.model:
                self.model = self.model.cuda()
            self.criterion = self.criterion.cuda()

    def train(self):
        lr = self.params[C.LR]
        self._set_optimizer(0, lr)
        self.save_metadata()

        # For Debuging
        # self.dataloader = list(self.dataloader)[:10]
        # self.dev_loader = list(self.dev_loader)[:5]
        # self.test_loader = list(self.test_loader)[:5]

        self.logger.log('Evaluating on DEV before epoch : 0')
        dev_loss = self.eval(self.dev_loader, C.DEV_TYPE, epoch=-1)

        # Add train loss entry for a corresponding dev loss entry before epoch 0
        self.loss.reset()
        self.metrics.add_loss(self.loss, C.TRAIN_TYPE)

        for epoch in range(self.start_epoch, self.params[C.EPOCHS]):
            self.logger.log('\n  --- STARTING EPOCH : %d --- \n' % epoch)

            # refresh loss, perplexity 
            self.loss.reset()
            for batch_itr, inputs in enumerate(tqdm(self.dataloader)):
                answer_seqs, quesion_seqs, review_seqs, \
                    answer_lengths = _extract_input_attributes(inputs, self.model_name)
                batch_loss, batch_perplexity = self.train_batch(
                    quesion_seqs,
                    review_seqs,
                    answer_seqs,
                    answer_lengths
                )
                if batch_itr % self.print_every == 0:
                    self.logger.log('\n\tMean [TRAIN] Loss for batch %d = %.2f' % (batch_itr, batch_loss))
                    self.logger.log('\tMean [TRAIN] Perplexity for batch %d = %.2f' % (batch_itr, batch_perplexity))

            logger.log('\n  --- END OF EPOCH : %d --- \n' % epoch)
            # Compute epoch loss and perplexity
            self.metrics.add_loss(self.loss, C.TRAIN_TYPE)

            # Save model periodically
            if epoch % self.save_model_every == 0:
                self.save_model(epoch)

            # Eval on dev set
            self.logger.log('\nStarting evaluation on DEV at end of epoch: %d' % epoch)
            dev_loss = self.eval(self.dev_loader, C.DEV_TYPE, epoch=epoch)
            self.logger.log('Finished evaluation on DEV')

            # Update lr is the val loss increases
            if dev_loss > prev_dev_loss:
                self._decay_lr(epoch, self.params[C.LR_DECAY])
                # lr *= self.params[C.LR_DECAY]
                # self._set_optimizer(epoch, lr=lr)
            prev_dev_loss = dev_loss

            # Save the best model till now
            if dev_loss == self.min_dev_loss:
                # Using epoch = -1 as best epoch
                self.save_model(-1)


    def train_batch(self, 
            quesion_seqs,
            review_seqs,
            answer_seqs,
            answer_lengths
        ):
        # Set model in train mode
        self.model.train(True)

        # Zero grad and teacher forcing
        self.optimizer.zero_grad()
        teacher_forcing = np.random.random() < self.params[C.TEACHER_FORCING_RATIO]

        # run forward pass
        loss, perplexity, _, _ = self._forward_pass(quesion_seqs, review_seqs, answer_seqs, teacher_forcing)

        # gradient computation
        loss.backward()

        # update parameters
        params = itertools.chain.from_iterable([g['params'] for g in self.optimizer.param_groups])
        torch.nn.utils.clip_grad_norm(params, self.params[C.GLOBAL_NORM_MAX])
        self.optimizer.step()

        return loss.data[0], perplexity

    def eval(self, dataloader, mode, output_filename=None, epoch=0):

        self.model.eval()

        if not dataloader:
            raise 'No [%s] Dataset' % mode
        else:
            self.logger.log('Evaluating on [%s] dataset' % mode)

        compute_loss = mode != C.TEST_TYPE
        if compute_loss:
            self.loss.reset()

        for batch_itr, inputs in tqdm(enumerate(dataloader)):
            answer_seqs, quesion_seqs, review_seqs, \
                answer_lengths = _extract_input_attributes(inputs, self.model_name)

            _, _, output_seq, output_lengths = self._forward_pass(
                quesion_seqs,
                review_seqs,
                answer_seqs,
                False,
                compute_loss=compute_loss
            )

            if mode == C.TEST_TYPE:
                output_seq = output_seq.data.cpu().numpy()
                with open(output_filename, 'a') as fp:
                    for seq_itr, length in enumerate(output_lengths):
                        length = int(length)
                        seq = output_seq[seq_itr, :length]
                        if seq[-1] == C.EOS_INDEX:
                            seq = seq[:-1]
                        tokens = self.vocab.token_list_from_indices(seq)
                        fp.write(' '.join(tokens) + '\n')

        if mode == C.DEV_TYPE:
            self.metrics.add_loss(self.loss, C.DEV_TYPE)
            # self._print_info(epoch, None, losses, perplexities, mode, self.logger)
        elif mode == C.TEST_TYPE:
            self.logger.log('Saving generated answers to file {0}'.format(output_filename))
        else:
            raise 'Unimplemented mode: %s' % mode
        return np.mean(np.array(losses))

    def _forward_pass(self,
            quesion_seqs,
            review_seqs,
            answer_seqs,
            teacher_forcing,
            compute_loss=True
        ):
        target_seqs, answer_seqs  = _var(answer_seqs), _var(answer_seqs)
        quesion_seqs = None if self.model_name == C.LM_ANSWERS else _var(quesion_seqs)
        review_seqs = map(_var, review_seqs) if self.model_name == C.LM_QUESTION_ANSWERS_REVIEWS else None

        # run forward pass
        outputs, output_seq, output_lengths = self.model(
            quesion_seqs,
            review_seqs,
            answer_seqs,
            target_seqs,
            teacher_forcing
        )

        # loss and gradient computation
        loss, perplexity = None, None
        if compute_loss:
            loss, perplexity = self.loss.eval_batch_loss(outputs, target_seqs)

        return loss, perplexity, output_seq, output_lengths


    def save_model(self, epoch):
        model_filename = '%s/%s_%d' % (self.save_dir, C.SAVED_MODEL_FILENAME, epoch)
        self.logger.log('Saving model (Epochs = %s)...' % epoch)
        torch.save(self.model.state_dict(), model_filename)
        torch.save({'optimizer': self.optimizer}, self._optimizer_filename(epoch))

    def load_model_optimizer(self, epoch):
        map_location = None if USE_CUDA else lambda storage, loc: storage # assuming the model was saved from a gpu machine
        model_filename = '%s/%s_%d' % (self.save_dir, C.SAVED_MODEL_FILENAME, epoch)
        self.logger.log('Loading model (Epochs = %s)...' % epoch)

        self.model.load_state_dict(torch.load(model_filename, map_location=map_location))
        self.optimizer = torch.load(self._optimizer_filename(epoch))['optimizer']
        if self.params[C.RESUME_LR] is not None:
            for param_group in self.optimizer.param_groups:
                param_group['lr'] *= self.params[C.RESUME_LR]
            
    def _optimizer_filename(self, epoch):
        return '%s/%s_%d.pt' % (self.save_dir, C.SAVED_OPTIMIZER_FILENAME, epoch)

    def _output_filename(self, epoch):
        _ensure_path(self.save_dir)
        return '%s/generated_answers_%d.txt' % (self.save_dir, epoch)

    def save_metadata(self):
        _ensure_path(self.save_dir)
        params_filename = '%s/%s' % (self.save_dir, C.SAVED_PARAMS_FILENAME)
        vocab_filename = '%s/%s' % (self.save_dir, C.SAVED_VOCAB_FILENAME)
        architecture_filename = '%s/%s' % (self.save_dir, C.SAVED_ARCHITECTURE_FILENAME)

        self.logger.log('Saving params in file: %s' % params_filename)
        with open(params_filename, 'w') as fp:
            json.dump(self.params, fp, indent=4, sort_keys=True)

        self.logger.log('Saving vocab in file: %s' % vocab_filename)
        with open(vocab_filename, 'wb') as fp:
            pickle.dump(self.vocab, fp, pickle.HIGHEST_PROTOCOL)

        self.logger.log('Saving architecture in file: %s' % architecture_filename)
        with open(architecture_filename, 'w') as fp:
            fp.write(str(self.model))

    def _save_dir(self, time):
        time_str = time.strftime('%Y-%m-%d-%H-%M-%S')
        return '%s/%s/%s/%s' % (C.BASE_PATH, self.params[C.CATEGORY], self.params[C.MODEL_NAME], time_str)

    def _set_optimizer(self, epoch, lr):
        self.optimizer = optim.Adam(self.model.parameters(), lr=lr)
        self.logger.log('Setting Learning Rate = %.9f (Epoch = %d)' % (lr, epoch))

    def _decay_lr(self, epoch, decay_factor):
        self.logger.log('Decaying learning rate by %.3f (Epoch = %d)' % (decay_factor, epoch))
        for param_group in self.optimizer.param_groups:
            param_group['lr'] *= decay_factor

def _set_random_seeds(seed):
    np.random.seed(seed)
    torch.manual_seed(seed)

def _ensure_path(path):
    if not os.path.exists(path):
        os.makedirs(path)

def _perplexity_from_loss(loss):
    return np.exp(loss)

def _var(variable):
    dtype = torch.cuda.LongTensor if USE_CUDA else torch.LongTensor
    return Variable(torch.LongTensor(variable).type(dtype))

def _extract_input_attributes(inputs, model_name):
    if model_name == C.LM_ANSWERS:
        answer_seqs, answer_lengths = inputs
        quesion_seqs, review_seqs = None, None
    elif model_name == C.LM_QUESTION_ANSWERS:
        (answer_seqs, answer_lengths), quesion_seqs = inputs
        review_seqs = None
    elif model_name == C.LM_QUESTION_ANSWERS_REVIEWS:
        (answer_seqs, answer_lengths), quesion_seqs, review_seqs = inputs
    else:
        raise 'Unimplemented model: %s' % model_name

    return answer_seqs, quesion_seqs, review_seqs, answer_lengths

def hsizes(params, model_name):
    r_hsize, q_hsize, a_hsize = params[C.HDIM_R], params[C.HDIM_Q], params[C.HDIM_A]
    if model_name == C.LM_QUESTION_ANSWERS:
        assert a_hsize == q_hsize
    if model_name == C.LM_QUESTION_ANSWERS_REVIEWS:
        assert a_hsize == r_hsize + q_hsize
    return r_hsize, q_hsize, a_hsize
