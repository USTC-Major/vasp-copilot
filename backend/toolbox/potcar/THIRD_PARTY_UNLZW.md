# Third-party decoder notice

`_unlzw.py` is a VASP-Copilot modified derivative of `unlzw3` version 0.2.3, from its
`unlzw3/unlzw.py` module. The upstream wheel is identified by SHA-256
`7760fb4f3afa1225623944c061991d89a061f7fb78665dbc4cddfdb562bb4a8b` and was
published at:

https://files.pythonhosted.org/packages/4d/fb/617af9b317ac75f5663285d3a3cc38903a76d63c6e7397768307545f4ff4/unlzw3-0.2.3-py3-none-any.whl

The derivative accepts only `bytes` or `bytearray` input and adds input-size,
output-size, and elapsed-time limits. The decoding algorithm is otherwise
retained from the upstream implementation. The upstream license text below is
reproduced verbatim from that wheel's `LICENSE.txt`.

```text
Written by Brandon Owen, May 2016, brandon.owen@hotmail.com
Adapted from original work by Mark Adler - orginal copyright notice below

Copyright (C) 2014, 2015 Mark Adler
This software is provided 'as-is', without any express or implied
warranty.  In no event will the authors be held liable for any damages
arising from the use of this software.
Permission is granted to anyone to use this software for any purpose,
including commercial applications, and to alter it and redistribute it
freely, subject to the following restrictions:
1. The origin of this software must not be misrepresented; you must not
claim that you wrote the original software. If you use this software
in a product, an acknowledgment in the product documentation would be
appreciated but is not required.
2. Altered source versions must be plainly marked as such, and must not be
misrepresented as being the original software.
3. This notice may not be removed or altered from any source distribution.
Mark Adler
madler@alumni.caltech.edu
```
