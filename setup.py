
from setuptools import setup

setup(
    name='smttask',
    version='0.1.0.dev1',
    description="Task wrapper for using Sumatra API",

    author="Alexandre René",
    author_email="arene010@uottawa.ca",

    license='MIT',

    classifiers=[
      'Development Status :: 3 - Alpha',
      'Intended Audience :: Science/Research',
      'Programming Language :: Python :: 3',
      'Programming Language :: Python :: 3 :: Only',
      'Topic :: Scientific/Engineering'
    ],

    packages=['smttask'],

    install_requires=['attrdict',
                      'sumatra[git]>=0.7.4', # TCW 8/3/20 changed from 0.8
                      #'psycopg2',
                      'mackelab-toolbox',
                      'parameters',
                      'click'
                      ],

    entry_points='''
        [console_scripts]
        smttask=smttask.ui:cli
    ''',
)
