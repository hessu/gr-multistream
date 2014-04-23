
gr-multistream - rtlstr multiple demodulators to icecast
===========================================================

This little script runs multiple FM or AM demodulators and feeds the results
to individual icecast MP3 streams.  It can be used to feed all the 2-meter
VHF repeaters to an icecast server with a single rtlsdr USB stick.

It's a bit unpolished, but it sort of works for me. I'm a bit busy right
now, so if you have a bug to report, please fix the bug first and do a pull
request on github, I'll be happy to merge your fix. Thanks!


Prerequirements
-----------------

* An user who is familiar with python and gnuradio.
* A working gnuradio installation, with rtlsdr drivers working.
* The 'lame' MP3 encoder needs to be installed, too.


Execution
-----------

    python gr-multistream.py --freqset RNET-OH2RCH=145787,APRS-144800=144800 \
        --icecast icecast.example.com --icepw passwordhere

--freqset defines a comma-separated list of name-frequency pairs. Frequency
(144800 in the above example) is given in kilohertz. The name (APRS-144800)
is the stream name used for the Icecast server - you might want to embed the
frequency in the stream name to make it visible on Icecast.

Icecast TCP server port is currently hardcoded as 8000. Sorry for that. But
it's a script, so it's not really hardcoded.

Other parameters:

 * --dev N specifies the N-th (0-based) device to be used. --dev 0 is the
   first hardware device, --dev 1 is the second stick.
 * --gain 20 sets hardware frontend gain to 20 dB.
 * --mode am switches to AM.
 * --audio-output "Audio device name" enables mixdown of all demodulated
   streams, and output of the whole mess to an audio device.


Issues to be fixed later
--------------------------

The squelch isn't quite right or optimal. For FM, it really should set up a
proper traditional high-pass-filter + level metering squelch.

It eats a lot of CPU. The filters could probably be adjusted to do a "good
enough" job with "low enough" CPU consumption.

The rtlstr stick now runs at 2 MHz / 2 Msamples/sec rate all the time. Could
use a lower sample rate if the actual required bandwidth is lower.  My stick
runs really hot (measured 80C with a fluke IR meter!), maybe lowering the
sample rate helps.

It creates a named pipe for every stream, it's used for feeding the audio
samples to the MP3 encoder.  That probably only works nicely on Unix-like
systems.  I was in a hurry, and didn't get around to figuring out how to
implement a custom sink block in Python, which in turn could be used to
implement a pipe to stdin of an external program.  A 'gr-pipe' module
example exists, but it's not in the main gnuradio distribution.  So I just
used the file sink block to write in the named pipe.

The named pipes are created in current working directory, and they're not
even deleted automatically when the program quits.

