#!/opt/local/bin/python2.7
#!/usr/bin/env python
#
# Usage: python gr-multistream.py --freqset RNET-OH2RCH=145787,APRS-144800=144800,2M-145500=145500
#
#
# Copyright 2005-2007,2011,2012 Free Software Foundation, Inc.
#
# GNU Radio is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
#
# GNU Radio is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GNU Radio; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 51 Franklin Street,
# Boston, MA 02110-1301, USA.
#

from gnuradio import gr, eng_notation
from gnuradio import blocks
from gnuradio import filter
from gnuradio import analog
from gnuradio import audio
import osmosdr
from gnuradio.eng_option import eng_option
from optparse import OptionParser
import sys
import math
import wx
import time
import os
import os.path
import subprocess
import threading
import select
import fcntl
import socket
from base64 import b64encode

class multistream(gr.top_block):
    def __init__(self, argv):
        gr.top_block.__init__(self)
        parser=OptionParser(option_class=eng_option)
        parser.add_option("-a", "--args", type="string", default="",
                          help="UHD device address args [default=%default]")
        parser.add_option("", "--dev", type="int", default=0,
	                  help="Device ID (0, 1, 2...)")
        parser.add_option("-f", "--freqset", type="string", default="",
                          help="Frequency set key=freq,key=freq,... in kHz")
        parser.add_option("-m", "--mode", type="string", default="fm",
                          help="Mode (am or fm)")
        parser.add_option("-g", "--gain", type="eng_float", default=30.0,
                          help="set gain in dB (default is maximum)")
        parser.add_option("-V", "--volume", type="eng_float", default=1.0,
                          help="set volume (default is midpoint)")
        parser.add_option("-i", "--icecast", type="string", default="",
                          help="Icecast base URL")
        parser.add_option("-p", "--icepw", type="string", default="",
                          help="Icecast source password")
        parser.add_option("-O", "--audio-output", type="string", default="",
                          help="pcm device name.  E.g., hw:0,0 or surround51 or /dev/dsp")

        (options, args) = parser.parse_args()
        if len(args) != 0:
            parser.print_help()
            sys.exit(1)

        self.vol = 0.8
        self.freqs = self.parse_freqset(options.freqset)
        
        self.freq_corr = 0
        self.rf_gain = options.gain # around 20
        self.if_gain = 7
        self.bb_gain = 10.0
        self.bandwidth = 2400000
        self.channel_bw = 10000
        
        freq_min = min(self.freqs.values())
        freq_max = max(self.freqs.values())
        
        print "Frequencies:"
        for k in self.freqs:
            print "  %s: %.3f MHz" % (k, self.freqs[k]/1000000.0)
        
        required_bw = freq_max - freq_min
        
        print "Required bandwidth: %.3f MHz" % (required_bw/1000000.0)
        if required_bw > self.bandwidth:
            print "Required bandwidth %.3f MHz larger than maximum BW %.3f MHz" % (required_bw/1000000, self.bandwidth/100000)
            return None
        
        # offset center frequency so that it's not on a monitored frequency
        self.center_freq = freq_min + (required_bw/2)
        for f in self.freqs.values():
            if abs(f - self.center_freq) < self.channel_bw:
                self.center_freq += self.channel_bw
            
        print "Center frequency: %.3f MHz" % self.center_freq
        
        print ""
        # build graph
        arg_s = "rtl=%d" % options.dev
        u = self.u = osmosdr.source(args=arg_s)
        
        u.set_center_freq(self.center_freq, 0)
        u.set_freq_corr(self.freq_corr, 0)
        u.set_dc_offset_mode(0, 0) # 0-2: Off-Manual-Automatic
        u.set_iq_balance_mode(0, 0) # 0-2: Off-Manual-Automatic
        u.set_gain_mode(False, 0) # 0-1: Manual, Automatic
        u.set_gain(self.rf_gain, 0)
        u.set_if_gain(self.if_gain, 0)
        u.set_bb_gain(self.bb_gain, 0)
        u.set_antenna("all", 0)
        u.set_sample_rate(1024e3*2)
        u.set_bandwidth(self.bandwidth, 0)
        
        dev_rate = self.u.get_sample_rate()
        demod_rate = 64e3
        audio_rate = 32e3
        chanfilt_decim = int(dev_rate // demod_rate)
        audio_decim = int(demod_rate // audio_rate)
        
        print "Device rate %d, bandwidth %.3f MHz" % (dev_rate, self.bandwidth / 1000000.0)
        print "Demod rate %d, audio rate %d" % (demod_rate, audio_rate)
        
        if options.mode == 'am':
            chan_filt_coeffs = filter.firdes.low_pass_2(1,          # gain
                                                    dev_rate,  # sampling rate
                                                    8e3,        # passband cutoff
                                                    2e3,        # transition bw
                                                    60)         # stopband attenuation
        else:
            print "FM filter"
            chan_filt_coeffs = filter.firdes.low_pass_2(1,          # gain
                                                    dev_rate,  # sampling rate
                                                    16e3,        # passband cutoff
                                                    3e3,        # transition bw
                                                    60)         # stopband attenuation
        
        audio_filt_coeffs = filter.firdes.low_pass_2(1,          # gain
                                                     demod_rate, # sampling rate
                                                     7e3,        # passband cutoff
                                                     2e3,        # transition bw
                                                     60)         # stopband attenuation
        demodulators = []
        
        for k in self.freqs:
            f = self.freqs[k]
            print "Setting up %s: %.3f MHz" % (k, f / 1000000.0)
            
            if_freq = f - self.center_freq
            chan_filt = filter.freq_xlating_fir_filter_ccf(chanfilt_decim,
                                                                  chan_filt_coeffs,
                                                                  if_freq,
                                                                  dev_rate)
            
            agc = analog.agc_cc(0.1, 1, 1)
            
            if options.mode == 'am':
                demod = blocks.complex_to_mag()
                
                squelch = analog.standard_squelch(dev_rate/10)
                sq_range = squelch.squelch_range()
                sq = (sq_range[0] + sq_range[1])/2
                sq = 0.7
                print "Squelch: range %.1f ... %.1f, using %.2f" % (sq_range[0], sq_range[1], sq)
                squelch.set_threshold(sq)
                
                audio_filt = filter.fir_filter_fff(audio_decim, audio_filt_coeffs)
                
                self.connect(chan_filt, agc, demod, squelch, audio_filt)
                last_block = audio_filt
            else:
                print "FM demod"
                demod = analog.demod_20k0f3e_cf(demod_rate, audio_decim)
                
                squelch = analog.pwr_squelch_cc(-50.0,    # Power threshold
                                            125.0/demod_rate,      # Time constant
                                            int(demod_rate/20),       # 50ms rise/fall
                                            False)                 # Zero, not gate output
                
                self.connect(chan_filt, squelch, agc, demod)
                last_block = demod
            
            demodulators.append([chan_filt, last_block])
            
            if options.icecast:
                # set up a file sink
                fname = self.setup_upstream_pipe(k, options)
                float_to_int = blocks.float_to_short(scale=7500.0)
                file_sink = blocks.file_sink(gr.sizeof_short, fname, append=True)
                self.connect(last_block, float_to_int, file_sink)
        
        self.adder = None
        if options.audio_output != "":
            self.volume_control = blocks.multiply_const_ff(self.vol)
            
            self.adder = blocks.add_ff(1)
            
            # sound card as final sink
            self.audio_sink = audio.sink(int (audio_rate),
                                          options.audio_output,
                                          False)  # ok_to_block

        # now wire it all together
        ch = 0
        for d in demodulators:
            self.connect(self.u, d[0])
            if self.adder:
                self.connect(d[1], (self.adder, ch))
            ch += 1
        
        if self.adder:
            self.connect(self.adder, self.volume_control, self.audio_sink)

        if options.gain is None:
            g = self.u.get_gain_range()
            # if no gain was specified, use the mid gain
            options.gain = (g.start() + g.stop())/2.0

        if options.volume is None:
            options.volume = 1.0
    
    def setup_upstream_pipe(self, key, options):
        fname = "pipe-%s.raw" % key
        
        # create a pipe
        try:
            os.unlink(fname)
        except OSError:
            pass
             
        os.mkfifo(fname)
        
        # set up mp3 encoder
        # bitrates (kbps): 32 40 48 56 64 80 96 112 128 160 192 224 256 320
        bitrate = 48
        samplerate = 32000
        cmd = ['lame', 
            '-b', str(bitrate), # mpeg bitrate
            '-m', 'm', # mode mono
            '-r', # raw samples
            '-s', str(samplerate), # sample rate
            '--flush', # flush as soon as possible
            '-h', # high-quality
            '--silent', # less verbose
            fname, '-']
        
        # maximum bufsize - lame is configured to flush quicker, anyway
        bufsize = 2048    
        pipe = subprocess.Popen(cmd, bufsize=bufsize, stdout=subprocess.PIPE).stdout
        # make pipe non-blocking
        fl = fcntl.fcntl(pipe, fcntl.F_GETFL)
        fcntl.fcntl(pipe, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        
        # set up a thread to read from the pipe
        thread = threading.Thread(target=self.upstream_thread, args=(key, pipe, options, samplerate, bitrate))
        thread.daemon = True
        thread.start()
        
        return fname
    
    def upstream_thread(self, key, pipe, options, samplerate, bitrate):
        """
        Read mpeg data stream from pipe, upload to server with low latency
        """
        
        # TODO: error handling, never fail - retry and rewire
        poll = select.poll()
        poll.register(pipe, select.POLLIN|select.POLLERR)
        
        ice = None
        last_connect = 0
        
        while True:
            r = poll.poll(1000)
            if len(r) < 1:
                print "... poll timeout"
                continue
                
            fd, ev = r[0]
            d = pipe.read(4096)
            #print "read %d" % len(d)
            
            if ice == None and time.time() - last_connect > 4:
                # Connect to icecast
                print "... connecting"
                last_connect = time.time()
                try:
                    ice = self.icecast_connect(options, key, samplerate, bitrate)
                    print "... connected!"
                except Exception:
                    ice = None
            
            if ice != None:
                try:
                    ice.send(d)
                except Exception:
                    try:
                        ice.close()
                    except Exception:
                        pass
                    ice = None
            
    def icecast_connect(self, options, key, samplerate, bitrate):
        """
        Connect to icecast
        """
        
        mountpoint = "/%s" % key
        
        # format a dict as HTTP request headers, but with configurable line endings
        # (since icecast wants \n instead of \r\n in some places)
        def request_format(request, line_separator="\n"):
            return line_separator.join(["%s: %s" % (key, str(val)) for (key, val) in request.items()])
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((options.icecast, 8000))
        s.sendall("SOURCE %s ICE/1.0\n%s\n%s\n\n" % (
            mountpoint,
            request_format({
                'content-type': 'audio/mpeg',
                'Authorization': 'Basic ' + b64encode("source:" + options.icepw),
                'User-Agent': "gr-multistream"
            }),
            request_format({
                'ice-name': key,
                'ice-genre': 'AM',
                'ice-bitrate': bitrate,
                'ice-private': 0,
                'ice-public': 0,
                'ice-description': key,
                'ice-audio-info': "ice-samplerate=%d;ice-bitrate=%d;ice-channels=1" %
                    (samplerate, bitrate)
            })
        ))
        
        response = s.recv(4096)
        if len(response) == 0:
            raise "No response from icecast server"
        
        if response.find(r"HTTP/1.0 200 OK") == -1:
            raise "Server response: %s" % response
        
        return s
        
    def parse_freqset(self, s):
        """
        Parse a set of frequecies from options
        """
        
        out = {}
        
        for kv in s.split(','):
            key, freq = kv.split('=')
            out[key] = int(float(freq)*1000.0)
        
        return out

rx = multistream(sys.argv)
rx.start()
while True:
    time.sleep(2)
    

