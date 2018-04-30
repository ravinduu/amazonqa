"""Seq2seq model for language models
"""

import numpy as np
import torch
import torch.nn as nn

import constants as C

from models.DecoderRNN import DecoderRNN
from models.EncoderRNN import EncoderRNN
from models.BaseRNN import BaseRNN


class Seq2Seq(nn.Module):

    def __init__(self, vocab_size, h_size, max_len, n_layers, dropout_p, mode):
        super(Seq2Seq, self).__init__()

        r_hsize, q_hsize, a_hsize = h_size

        self.mode = mode

        if mode == C.LM_ANSWERS:
            self.question_encoder = None
        else:
            self.question_encoder = EncoderRNN(vocab_size=vocab_size, max_len=max_len, hidden_size=q_hsize,
                        n_layers=n_layers, dropout_p=dropout_p)


        if mode == C.LM_QUESTION_ANSWERS_REVIEWS:
            self.reviews_encoder = EncoderRNN(vocab_size=vocab_size, max_len=max_len, hidden_size=r_hsize,
                        n_layers=n_layers, dropout_p=dropout_p)
        else:
            self.reviews_encoder = None


        if self.mode == C.LM_QUESTION_ANSWERS:
            assert q_hsize == a_hsize
        if self.mode == C.LM_QUESTION_ANSWERS_REVIEWS:
            assert a_hsize == q_hsize + r_hsize

        self.decoder = DecoderRNN(vocab_size=vocab_size, max_len=max_len, hidden_size=a_hsize,
                            n_layers=n_layers, dropout_p=dropout_p, sos_id=C.SOS_INDEX, eos_id=C.EOS_INDEX)


    def forward(self,
        question_seqs,
        review_seqs,
        answer_seqs,
        target_seqs,
        teacher_forcing
    ):
        #print(question_seqs, review_seqs, answer_seqs, target_seqs)
        if self.mode == C.LM_ANSWERS:
            d_hidden = None
        elif self.mode == C.LM_QUESTION_ANSWERS:
            _, d_hidden = self.question_encoder(question_seqs)
        elif self.mode == C.LM_QUESTION_ANSWERS_REVIEWS:
            _, question_hidden = self.question_encoder(question_seqs)
            reviews_hidden = [self.reviews_encoder(seq)[1] for seq in review_seqs]
            reviews_hidden = list(map(_mean, zip(*reviews_hidden)))
            d_hidden = tuple(torch.cat([q_h, r_h], 2) for q_h, r_h in zip(question_hidden, reviews_hidden))
        else:
            raise 'Unimplemented model: %s' % self.mode

        return self.decoder(inputs=target_seqs, encoder_hidden=d_hidden, 
            encoder_outputs=None, teacher_forcing_ratio=teacher_forcing)

def _mean(vars):
    return torch.mean(torch.cat([i.unsqueeze(0) for i in vars], 0), 0)

# def _cat_hidden(h1, h2):
#     return (torch.cat([h])