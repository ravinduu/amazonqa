import os
import torch
import string
import pandas as pd
from tqdm import tqdm

import constants as C
from data.vocabulary import Vocabulary
import ipdb as pdb
from data import review_utils

DEBUG = False

class AmazonDataset(object):
    def __init__(self, params):
        self.model = params[C.MODEL_NAME]

        category = params[C.CATEGORY]

        self.max_question_len = params[C.MAX_QUESTION_LEN]
        self.max_answer_len = params[C.MAX_ANSWER_LEN]
        self.max_review_len = params[C.MAX_REVIEW_LEN]
        self.review_select_num = params[C.REVIEW_SELECT_NUM]
        self.review_select_mode = params[C.REVIEW_SELECT_MODE]

        self.max_vocab_size = params[C.VOCAB_SIZE]

        train_path = '%s/train-%s.pickle' % (C.INPUT_DATA_PATH, category)
        self.vocab = self.create_vocab(train_path)
        self.train = self.get_data(train_path)

        val_path = '%s/val-%s.pickle' % (C.INPUT_DATA_PATH, category)
        self.val = self.get_data(val_path)

        test_path = '%s/test-%s.pickle' % (C.INPUT_DATA_PATH, category)
        self.test = self.get_data(test_path)

    def tokenize(self, text):
        punctuations = string.punctuation.replace("\'", '')

        for ch in punctuations:
            text = text.replace(ch, " " + ch + " ")

        tokens = text.split()

        for i in range(len(tokens)):
            token = tokens[i]
            if token.isupper() == False:
                tokens[i] = token.lower()
        return tokens


    def truncate_tokens(self, text, max_length):
        tokens = self.tokenize(text)
        if len(tokens) > max_length:
            tokens = tokens[:max_length]
        return tokens


    def create_vocab(self, train_path):
        vocab = Vocabulary(self.max_vocab_size)
        assert os.path.exists(train_path)
        total_tokens = 0

        with open(train_path, 'rb') as f:
            dataFrame = pd.read_pickle(f)
            if DEBUG:
                dataFrame = dataFrame.iloc[:5]

        for _, row in dataFrame.iterrows():
            questionsList = row[C.QUESTIONS_LIST]
            for question in questionsList:
                tokens = self.truncate_tokens(question[C.TEXT], self.max_question_len)
                vocab.add_sequence(tokens)

                for answer in question[C.ANSWERS]:
                    tokens = self.truncate_tokens(answer[C.TEXT], self.max_answer_len)
                    total_tokens += len(tokens)
                    vocab.add_sequence(tokens)

            reviewsList = row[C.REVIEWS_LIST]
            for review in reviewsList:
                tokens = self.truncate_tokens(review[C.TEXT], self.max_review_len)
                vocab.add_sequence(tokens)

        print("Train: No. of Tokens = %d, Vocab Size = %d" % (total_tokens, vocab.size))
        return vocab


    def get_data(self, path):
        answersDict = []
        questionsDict = []
        reviewsDict = []
        questionAnswersDict = []

        questionId = -1
        reviewId = -1
        answerId = -1
        data = []

        print("Creating Dataset from " + path)
        assert os.path.exists(path)

        with open(path, 'rb') as f:
            dataFrame = pd.read_pickle(f)
            if DEBUG:
                dataFrame = dataFrame.iloc[:5]

        for _, row in tqdm(dataFrame.iterrows()):
            tuples = []
            questionsList = row[C.QUESTIONS_LIST]
            for question in questionsList:
                tokens = self.truncate_tokens(question[C.TEXT], self.max_question_len)
                ids = self.vocab.indices_from_token_list(tokens)
                questionsDict.append(ids)
                questionId += 1

                answerIdsList = []
                for answer in question[C.ANSWERS]:
                    tokens = self.truncate_tokens(answer[C.TEXT], self.max_answer_len)
                    ids = self.vocab.indices_from_token_list(tokens)
                    answersDict.append(ids)
                    answerId += 1
                    answerIdsList.append(answerId)

                    if self.model == C.LM_ANSWERS:
                        tuples.append((answerId,))
                    else:
                        tuples.append((answerId, questionId))
                questionAnswersDict.append(answerIdsList)

            if self.model == C.LM_QUESTION_ANSWERS_REVIEWS:
                reviewsList = row[C.REVIEWS_LIST]
                reviewsList = review_utils.select_reviews(reviewsList, self.review_select_mode, self.review_select_num)
                reviewsDictList = []
                for review in reviewsList:
                    tokens = self.truncate_tokens(review[C.TEXT], self.max_review_len)
                    ids = self.vocab.indices_from_token_list(tokens)
                    reviewsDict.append(ids)
                    reviewId += 1
                    reviewsDictList.append(reviewId)

                for i in range(len(tuples)):
                    tuples[i] = tuples[i] + (reviewsDictList,)

            data.extend(tuples)

        assert(len(answersDict) == answerId+1)
        assert(len(questionsDict) == questionId+1)
        assert(len(reviewsDict) == reviewId+1)
        print("Number of samples in the data = %d" % (len(data)))

        return (answersDict, questionsDict, questionAnswersDict, reviewsDict, data)