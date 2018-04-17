import logging
import pickle

import numpy as np
from sklearn.manifold import TSNE

from linguistic_style_transfer_model.config import global_config

logger = logging.getLogger(global_config.logger_name)


def generate_plot_coordinates(label_mapped_embeddings, coordinates_path):
    embeddings = list()
    markers = list()
    for label in label_mapped_embeddings:
        markers.append(len(embeddings))
        for embedding in label_mapped_embeddings[label]:
            embeddings.append(embedding)
    markers.append(len(embeddings))
    logger.debug("markers: {}".format(markers))

    embeddings = np.asarray(a=embeddings)
    logger.info("Extracted individual embeddings of shape {}".format(embeddings.shape))

    logger.info("Learning plot co-ordinates")
    coordinates = \
        TSNE(n_components=2).fit_transform(X=embeddings) \
            if embeddings.shape[1] != 2 else embeddings
    logger.debug("coordinates.shape: {}".format(coordinates.shape))

    with open(coordinates_path, 'wb') as pickle_file:
        pickle.dump((coordinates, markers), pickle_file)

    logger.info("Dumped T-SNE co-ordinates")