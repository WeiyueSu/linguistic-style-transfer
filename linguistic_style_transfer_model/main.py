import argparse
import pickle
import sys
from datetime import datetime as dt
from random import randint

import numpy as np
import tensorflow as tf

from linguistic_style_transfer_model.config import global_config, model_config
from linguistic_style_transfer_model.models import adversarial_autoencoder
from linguistic_style_transfer_model.utils import bleu_scorer, data_postprocessor, \
    data_preprocessor, log_initializer, word_embedder

logger = None


def get_data(text_file_path, vocab_size, label_file_path):
    padded_sequences, text_sequence_lengths, word_index, max_sequence_length, actual_sequences = \
        data_preprocessor.get_text_sequences(text_file_path, vocab_size)
    logger.debug("text_sequence_lengths: {}".format(text_sequence_lengths.shape))
    logger.debug("padded_sequences: {}".format(padded_sequences.shape))

    sos_index = word_index[global_config.sos_token]
    eos_index = word_index[global_config.eos_token]
    data_size = padded_sequences.shape[0]

    one_hot_labels, num_labels, label_sequences = data_preprocessor.get_labels(label_file_path)
    logger.debug("one_hot_labels.shape: {}".format(one_hot_labels.shape))

    return num_labels, max_sequence_length, vocab_size, sos_index, eos_index, padded_sequences, \
           one_hot_labels, text_sequence_lengths, label_sequences, data_size, word_index, actual_sequences


def get_average_label_embeddings(data_size, label_sequences):
    with open(global_config.all_style_embeddings_path, 'rb') as pickle_file:
        all_style_embeddings = pickle.load(pickle_file)

    style_embeddings = np.asarray(all_style_embeddings)

    label_embedding_map = dict()
    for i in range(data_size - (data_size % model_config.batch_size)):
        label = label_sequences[i][0]
        if label not in label_embedding_map:
            label_embedding_map[label] = list()
        label_embedding_map[label].append(style_embeddings[i])

    with open(global_config.label_mapped_style_embeddings_path, 'wb') as pickle_file:
        pickle.dump(label_embedding_map, pickle_file)
    logger.debug("Pickled label mapped style embeddings")

    average_label_embeddings = dict()
    for label in label_embedding_map:
        average_label_embeddings[label] = np.mean(label_embedding_map[label], axis=0)

    return average_label_embeddings


def flush_ground_truth_sentences(actual_sequences, start_index, final_index, max_sequence_length,
                                 word_index, inverse_word_index, timestamped_file_suffix):
    actual_sequences = actual_sequences[start_index:final_index]

    actual_sequences = tf.keras.preprocessing.sequence.pad_sequences(
        actual_sequences, maxlen=max_sequence_length, padding='post', truncating='post',
        value=word_index[global_config.eos_token])

    actual_word_lists = \
        [data_postprocessor.generate_words_from_indices(x, inverse_word_index)
         for x in actual_sequences]

    actual_sentences = [" ".join(x) for x in actual_word_lists]

    output_file_path = "output/actual_sentences_{}.txt".format(timestamped_file_suffix)
    with open(output_file_path, 'w') as output_file:
        for sentence in actual_sentences:
            output_file.write(sentence + "\n")

    return actual_word_lists


def execute_post_inference_operations(word_index, actual_word_lists, generated_sequences,
                                      final_sequence_lengths, max_sequence_length,
                                      inverse_word_index, timestamped_file_suffix, mode):
    logger.debug("Minimum generated sentence length: {}".format(min(final_sequence_lengths)))

    trimmed_generated_sequences = [x[:y] for (x, y) in zip(generated_sequences, final_sequence_lengths)]

    generated_sequences = tf.keras.preprocessing.sequence.pad_sequences(
        trimmed_generated_sequences, maxlen=max_sequence_length, padding='post', truncating='post',
        value=word_index[global_config.eos_token])

    generated_word_lists = \
        [data_postprocessor.generate_words_from_indices(x, inverse_word_index)
         for x in generated_sequences]

    # Evaluate model scores
    bleu_scores = bleu_scorer.get_corpus_bleu_scores(
        [[x] for x in actual_word_lists], generated_word_lists)
    logger.info("bleu_scores: {}".format(bleu_scores))
    generated_sentences = [" ".join(x) for x in generated_word_lists]

    output_file_path = "output/generated_{}_{}.txt".format(mode, timestamped_file_suffix)
    with open(output_file_path, 'w') as output_file:
        for sentence in generated_sentences:
            output_file.write(sentence + "\n")


def get_word_embeddings(vocab_size, word_index, use_pretrained_embeddings, train_model):
    encoder_embedding_matrix = np.random.uniform(
        low=-0.05, high=0.05, size=(vocab_size, global_config.embedding_size)).astype(dtype=np.float32)
    logger.debug("encoder_embedding_matrix: {}".format(encoder_embedding_matrix.shape))

    decoder_embedding_matrix = np.random.uniform(
        low=-0.05, high=0.05, size=(vocab_size, global_config.embedding_size)).astype(dtype=np.float32)
    logger.debug("decoder_embedding_matrix: {}".format(decoder_embedding_matrix.shape))

    if train_model and use_pretrained_embeddings:
        logger.info("Loading pretrained embeddings")
        encoder_embedding_matrix, decoder_embedding_matrix = word_embedder.add_word_vectors_to_embeddings(
            word_index, global_config.word_vector_path, encoder_embedding_matrix,
            decoder_embedding_matrix, vocab_size)

    return encoder_embedding_matrix, decoder_embedding_matrix


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev-mode", action="store_true", default=False)
    parser.add_argument("--train-model", action="store_true", default=False)
    parser.add_argument("--infer-sequences", action="store_true", default=False)
    parser.add_argument("--generate-novel-text", action="store_true", default=False)
    parser.add_argument("--use-pretrained-embeddings", action="store_true", default=False)
    parser.add_argument("--training-epochs", type=int, default=10)
    parser.add_argument("--vocab-size", type=int, default=1000)
    parser.add_argument("--text-file-path", type=str, required=True)
    parser.add_argument("--label-file-path", type=str, required=True)
    parser.add_argument("--logging-level", type=str, default="INFO")

    args_namespace = parser.parse_args(argv)
    command_line_args = vars(args_namespace)

    global logger
    logger = log_initializer.setup_custom_logger(
        global_config.logger_name, command_line_args['logging_level'])

    if not (command_line_args['train_model'] or command_line_args['infer_sequences'] or
            command_line_args['generate_novel_text']):
        logger.info("Nothing to do. Exiting ...")
        sys.exit(0)

    # Retrieve all data
    logger.info("Reading data ...")
    num_labels, max_sequence_length, vocab_size, sos_index, eos_index, padded_sequences, \
    one_hot_labels, text_sequence_lengths, label_sequences, data_size, word_index, actual_sequences = \
        get_data(command_line_args['text_file_path'], command_line_args['vocab_size'],
                 command_line_args['label_file_path'])

    encoder_embedding_matrix, decoder_embedding_matrix = \
        get_word_embeddings(vocab_size, word_index, command_line_args['use_pretrained_embeddings'],
                            command_line_args['train_model'])

    # Build model
    logger.info("Building model architecture ...")
    network = adversarial_autoencoder.AdversarialAutoencoder(
        num_labels, max_sequence_length, vocab_size, sos_index, eos_index,
        encoder_embedding_matrix, decoder_embedding_matrix, padded_sequences,
        one_hot_labels, text_sequence_lengths, label_sequences)
    network.build_model()

    # Train and save model
    if command_line_args['train_model']:
        logger.info("Training model ...")
        sess = get_tensorflow_session()
        network.train(sess, data_size, command_line_args['training_epochs'])
        sess.close()
        logger.info("Training complete!")

    if command_line_args['infer_sequences'] or command_line_args['generate_novel_text']:
        samples_size = data_size - (data_size % model_config.batch_size)
        offset = 0
        logger.debug("Sampling range: {}-{}".format(offset, (offset + samples_size)))

        inverse_word_index = {v: k for k, v in word_index.items()}
        timestamped_file_suffix = dt.now().strftime("%Y%m%d%H%M%S")

        actual_word_lists = flush_ground_truth_sentences(
            actual_sequences, offset, offset + samples_size, max_sequence_length,
            word_index, inverse_word_index, timestamped_file_suffix)

        # Restore model and run inference
        if command_line_args['infer_sequences']:
            logger.info("Inferring test samples ...")
            sess = get_tensorflow_session()
            generated_sequences, final_sequence_lengths = \
                network.infer(sess, offset, samples_size)
            sess.close()
            execute_post_inference_operations(
                word_index, actual_word_lists, generated_sequences, final_sequence_lengths,
                max_sequence_length, inverse_word_index, timestamped_file_suffix,
                "reconstructed_sentences")
            logger.info("Inference complete!")

        # Enforce a particular style embedding and regenerate text
        if command_line_args['generate_novel_text']:
            logger.info("Generating novel text ...")
            random_style_choice = randint(1, num_labels)
            logger.debug("Style chosen: {}".format(random_style_choice))
            average_label_embeddings = get_average_label_embeddings(
                data_size, label_sequences)
            style_embedding = np.asarray(average_label_embeddings[random_style_choice])
            sess = get_tensorflow_session()
            generated_sequences, final_sequence_lengths =\
                network.generate_novel_sentences(sess, offset, samples_size, style_embedding)
            execute_post_inference_operations(
                word_index, actual_word_lists, generated_sequences, final_sequence_lengths,
                max_sequence_length, inverse_word_index, timestamped_file_suffix,
                "novel_sentences")
            logger.info("Generation complete!")


def get_tensorflow_session():
    gpu_options = tf.GPUOptions(allow_growth=True)
    config_proto = tf.ConfigProto(
        log_device_placement=False, allow_soft_placement=True,
        gpu_options=gpu_options)

    return tf.Session(config=config_proto)


if __name__ == "__main__":
    main(sys.argv[1:])